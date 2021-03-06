import random
import string
import logging
from django.contrib.auth.models import User
from mongoengine import *
from base import send_event

logger = logging.getLogger('lpgame')


class EnglishWords(Document):
    WORDS_COUNT = 60388

    word_id = IntField()
    word = StringField(max_length=30)

    @staticmethod
    def is_a_word(word):
        result = EnglishWords.objects(word=word).first()
        return result is not None


class Letter(EmbeddedDocument):
    letter_id = IntField()
    letter = StringField(max_length=1)
    gamer = IntField()


class PlayedWords(EmbeddedDocument):
    gamer = IntField()
    words = ListField(StringField(max_length=30))


class Game(Document):
    MAX_GAMERS = 2
    ended = BooleanField(default=False)
    gamers = ListField(IntField())
    played_words = ListField(EmbeddedDocumentField(PlayedWords))
    letters = ListField(EmbeddedDocumentField(Letter))
    session_id = StringField(max_length=20)
    current_player = IntField()
    winner_id = IntField()

    def end(self):
        self.ended = True
        self.save()

    def is_current_player(self, user_id):
        return user_id == self.current_player

    def change_current_player(self):
        for gamer in self.gamers:
            if self.is_current_player(gamer):
                continue
            else:
                self.current_player = gamer
                break
        self.save()

    def is_all_letters_played(self):
        counter = 0
        for letter in self.letters:
            if letter.gamer is not None:
                counter += 1
        return counter == len(self.letters)

    def score(self):
        result_score = {x: 0 for x in self.gamers}
        for letter in self.letters:
            if letter.gamer is None:
                continue
            result_score[letter.gamer] += 1
        return result_score

    def new_player(self, user):
        user_id = user.pk
        self.gamers.append(user_id)
        self.save()
        if len(self.gamers) == self.MAX_GAMERS:
            send_event('game_ready', {'opponent_name': user.get_full_name()}, self.session_id)

    def opponent(self, user_id):
        for gamer in self.gamers:
            if gamer != user_id:
                return User.objects.get(pk=gamer)
        return None

    def get_user_words(self, user_id):
        for words in self.played_words:
            if words.gamer == user_id:
                return words.words
        return []


    @property
    def winner(self):
        if not self.ended:
            raise Exception("No winner. Game in process")
        if self.winner_id is not None:
            return self.winner_id
        res = {x: 0 for x in self.gamers}
        for letter in self.letters:
            res[letter.gamer] += 1
        self.winner_id = max(res.iterkeys(), key=lambda k: res[k])
        self.save()
        return self.winner_id

    @classmethod
    def get_user_games(cls, user_id, ended=False):
        return cls.objects(gamers=user_id, ended=ended)


def clean_list(letters):
    for letter in string.ascii_lowercase:
        if letters.count(letter) >= 3 and len(letters) > 25:
            letters.remove(letter)
    if len(letters) > 25:
        letters = letters[:25]
    return letters


def generate_letters():
    letters = []
    while len(letters) <= 25:
        word_id = random.randint(1, EnglishWords.WORDS_COUNT)
        word = EnglishWords.objects.get(word_id=word_id).word
        letters += list(word)
    random.shuffle(letters)
    letters = clean_list(letters)
    return letters


def generate_game(user, session_id):
    game = Game(gamers=[user.pk], session_id=session_id)
    letters = generate_letters()
    for i, letter in enumerate(letters):
        game.letters.append(Letter(letter_id=i + 1, letter=letter))
    game.current_player = user.pk
    game.save()
    return game


def get_letter_by_id(game, letter_id):
    for letter in game.letters:
        if letter.letter_id == letter_id:
            return letter
    raise DoesNotExist('No such letter')


def send_event_on_user_turn(game, word, letters, user):
    letters_to_send = on_user_turn(game, word, letters, user)
    logger.debug("{} played word '{}' in game {}".format(
        user.username,
        word,
        game.session_id
    ))
    data = {
        'letters': letters_to_send,
        'score': game.score(),
        'word': word
    }
    if game.is_all_letters_played():
        game.end()
        logger.info("game {} has ended, the winner is {}".format(
            game.session_id,
            user.username
        ))
        data['winner'] = game.winner
        send_event('game_over', data, game.session_id, user.pk)
    else:
        send_event('new_turn', data, game.session_id, user.pk)


def on_user_turn(game, word, letters, user):
    user_words = None
    if not EnglishWords.is_a_word(word):
        logger.debug("'{}' is not a word".format(word))
        raise NotAWordException
    for played_words in game.played_words:
        if word in played_words.words:
            raise WordAlreadyUsedException
        if played_words.gamer == user.pk:
            user_words = played_words
    if user_words is not None:
        user_words.words.append(word)
    else:
        game.played_words.append(PlayedWords(gamer=user.pk, words=[word]))
    prepared_letters = []
    for letter in letters:
        db_letter = get_letter_by_id(game, letter)
        db_letter.gamer = user.pk
        prepared_letters.append(db_letter.letter_id)
    game.save()
    game.change_current_player()
    return prepared_letters


class WordAlreadyUsedException(Exception):
    pass


class NotAWordException(Exception):
    pass
