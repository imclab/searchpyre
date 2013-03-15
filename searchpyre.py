# -*- coding: utf-8 -*-
# Simple Python Search Engine built on Redis
# https://github.com/ebaum/searchpyre

__author__ = 'Eugene Baumstein'
__license__ = 'MIT'
__version__ = '0.1'


import re
import os
import math
import time
import datetime
import collections
import unicodedata
from redis import StrictRedis
from itertools import chain

_NON_WORDS = re.compile("[^a-z0-9' ]")
_KEY = re.compile('(\w+\.\w+)#([0-9]+):?(\w+)?') # 'app.model#1:word'
_ARTICLES = ('the', 'of', 'to', 'and', 'an', 'in', 'is', 'it', 'you', 'that', 'this')


def _unique(seq):
    seen = set()
    for item in seq:
        if item not in seen:
            seen.add(item)
            yield item


def _get_words(text, weighted=True):
    if not isinstance(text, basestring):
        return dict([(str(text), 1)])
    words = _NON_WORDS.sub(' ', text.lower()).split()
    words = [word for word in words if word not in _ARTICLES and len(word) > 1]
    if not weighted:
        return words
    words = map(lambda x: x if x.isdigit() else _double_metaphone(unicode(x)), words)
    words = [word for sublist in words for word in sublist if word]
    counts = collections.defaultdict(float)
    for word in words:
        counts[word] += 1
    return dict((word, count / len(words)) for word, count in counts.iteritems())


class _Result(dict):

    def __init__(self, *args, **kwargs):
        dict.__init__(self, *args, **kwargs)
        self._key = None
        self._instance = None

    def __hash__(self):
        app_dot_model, pk, _ = _KEY.match(self._key).groups()
        return hash(app_dot_model + pk)

    @property
    def instance(self):
        if self._key and self._instance is None:
            app_dot_model, pk, _ = _KEY.match(self._key).groups()
            model = get_model(*app_dot_model.split('.'))
            self._instance = model.objects.get(pk=pk)
        return self._instance


class Pyre(object):

    def __init__(self, *args, **kwargs):
        self.redis = StrictRedis(*args, **kwargs)

    def _map_results(self, keys):
        if not keys:
            return []
        pipe = self.redis.pipeline()
        for key in keys:
            app_dot_model, pk, _ = _KEY.match(key).groups()
            pipe.hgetall(app_dot_model + '#' + pk)
        results = []
        indexes = pipe.execute()
        for i, index in enumerate(indexes):
            for field, value in index.iteritems():
                if not value:
                    continue
                if value[0] == 'A':
                    index[field] = value[2:].split(',')
                elif value[0] == 'M':
                    index[field] = int(value[2:])
                elif value[0] == 'T':
                    index[field] = float(value[2:])
                elif value[0] == 'B':
                    index[field] = bool(value[2:])
                elif value[0] == 'I':
                    index[field] = int(value[2:])
                elif value[0] == 'F':
                    index[field] = float(value[2:])
                elif value[0] == 'S':
                    index[field] = value[2:]
                else:
                    print '\033[93m', 'ERROR', field, value, '\033[0m'
            result = _Result(index)
            result._key = keys[i]
            results.append(result)
        return results

    def get_all(self, model):
        keys = self.redis.keys(model._meta.app_label + '.' + model._meta.module_name + '#*')
        results = self._map_results(keys)
        return results

    def autocomplete(self, query):
        keys = ['a:' + key for key in _unique(_get_words(query, weighted=False))]
        if not keys:
            return []
        pipe = self.redis.pipeline()
        for key in keys:
            pipe.zrevrange(key, 0, -1, withscores=False)
        ikeys = [ikey for sublist in pipe.execute() for ikey in sublist]
        results = [result for result in _unique(self._map_results(ikeys))]
        return results

    def search(self, query, offset=0, count=10):
        keys = ['w:' + key for key in _get_words(query)]
        if not keys:
            return []
        indexed = max(self.redis.get('indexed'), 1)
        pipe = self.redis.pipeline()
        for key in keys:
            pipe.zcard(key)
        counts = pipe.execute()
        ranks = [max(math.log(float(indexed) / count, 2), 0) if count else 0 for count in counts]
        weights = dict((key, rank) for key, count, rank in zip(keys, counts, ranks) if count)
        if not weights:
            return []
        temp_key = 'temp:' + os.urandom(8).encode('hex')
        try:
            self.redis.zunionstore(temp_key, weights)
            keys = self.redis.zrevrange(temp_key, offset, offset+count-1, withscores=False)
        finally:
            self.redis.delete(temp_key)
        results = self._map_results(keys)
        return results


class SearchIndex(object):

    def __init__(self, *args, **kwargs):
        self.redis = StrictRedis(*args, **kwargs)

    def index(self, value, uid=None, key='text', autocompletion=False, **kwargs):
        if not uid:
            uid = self.redis.incr('indexed')
        self.redis.hset(uid, key, value)
        pipe = self.redis.pipeline()
        if autocompletion:
            for i, word in enumerate(_get_words(value, weighted=False)):
                for i, letter in enumerate(word):
                    if len(word) > i + 1:
                        pipe.zadd('a:' + word[:2+i], 0, uid+':'+word)
        else:
            for word, value in _get_words(value).iteritems():
                pipe.zadd('w:' + word, value, uid)
        pipe.execute()

    def index_autocomplete(self, value, uid=None, key='text'):
        self.index(value, uid, key, autocompletion=True)


if os.environ.get('DJANGO_SETTINGS_MODULE'):

    from django.db.models import Model
    from django.db.models.base import ModelBase
    from django.db.models.manager import Manager
    from django.db.models.loading import get_model

    class DjangoSearchIndex(SearchIndex):

        def __init__(self, source, **kwargs):
            self.source = source
            self.app_dot_model = source._meta.app_label + '.' + source._meta.module_name
            self.redis = StrictRedis(**kwargs)

        def index(self, *fields, **kwargs):
            if isinstance(self.source, ModelBase):
                instances = self.source.objects.all()
            elif isinstance(self.source, Model):
                instances = [self.source]
            else:
                raise ImportError('Only Model or Model instances are valid inputs')
            for instance in instances:
                if kwargs.get('everything'):
                    fields = chain( [field.name for field in instance._meta.fields],
                                    [field.name for field in instance._meta._many_to_many()] )
                for field in set(fields):
                    value = instance.__getattribute__(field)
                    if isinstance(value, Manager) and value.all():
                        value = 'A|' + ','.join([ str(obj.id) for obj in value.all() ])
                    elif isinstance(value, Model):
                        value = 'M|' + str(value.pk)
                    elif isinstance(value, datetime.date):
                        value = 'T|' + str(time.mktime(value.timetuple()))
                    elif isinstance(value, bool):
                        value = 'B|' + ('1' if value else '')
                    elif isinstance(value, int):
                        value = 'I|' + str(value)
                    elif isinstance(value, float):
                        value = 'F|' + str(value)
                    elif isinstance(value, basestring) and value:
                        value = 'S|' + value
                    else:
                        value = ''
                    super(DjangoSearchIndex, self).index(value,
                        self.app_dot_model + '#' + str(instance.id), field, **kwargs)

        def index_autocomplete(self, *fields, **kwargs):
            kwargs['autocompletion'] = True
            self.index(*fields, **kwargs)

else:

    class DjangoSearchIndex(SearchIndex):

        def __init__(self, *args, **kwargs):
            raise ImportError('DjangoSearchIndex only works in a django environment')


#https://github.com/dracos/double-metaphone
#http://atomboy.isa-geek.com/plone/Members/acoil/programing/double-metaphone/metaphone.py
# {{{
def _double_metaphone(st):
    """double_metaphone(string) -> (string, string or '')
    returns the double metaphone codes for given string - always a tuple
    there are no checks done on the input string, but it should be a single word or name."""
    vowels = ['A', 'E', 'I', 'O', 'U', 'Y']
    st = ''.join((c for c in unicodedata.normalize('NFD', st) if unicodedata.category(c) != 'Mn'))
    st = st.upper()  # st is short for string. I usually prefer descriptive over short, but this var is used a lot!
    is_slavo_germanic = (st.find('W') > -1 or st.find('K') > -1 or st.find('CZ') > -1 or st.find('WITZ') > -1)
    length = len(st)
    first = 2
    st = '-' * first + st + '------'  # so we can index beyond the begining and end of the input string
    last = first + length - 1
    pos = first     # pos is short for position
    pri = sec = ''  # primary and secondary metaphone codes
    # skip these silent letters when at start of word
    if st[first:first + 2] in ["GN", "KN", "PN", "WR", "PS"]:
        pos += 1
    # Initial 'X' is pronounced 'Z' e.g. 'Xavier'
    if st[first] == 'X':
        pri = sec = 'S'  # 'Z' maps to 'S'
        pos += 1
    # main loop through chars in st
    while pos <= last:
        #print str(pos) + '\t' + st[pos]
        ch = st[pos]  # ch is short for character
        # nxt (short for next characters in metaphone code) is set to  a tuple of the next characters in
        # the primary and secondary codes and how many characters to move forward in the string.
        # the secondary code letter is given only when it is different than the primary.
        # This is just a trick to make the code easier to write and read.
        nxt = (None, 1)  # default action is to add nothing and move to next char
        if ch in vowels:
            nxt = (None, 1)
            if pos == first:  # all init vowels now map to 'A'
                nxt = ('A', 1)
        elif ch == 'B':
            #"-mb", e.g", "dumb", already skipped over... see 'M' below
            if st[pos + 1] == 'B':
                nxt = ('P', 2)
            else:
                nxt = ('P', 1)
        elif ch == 'C':
            # various germanic
            if pos > first + 1 and st[pos - 2] not in vowels and st[pos - 1:pos + 2] == 'ACH' and \
               st[pos + 2] not in ['I'] and (st[pos + 2] not in ['E'] or st[pos - 2:pos + 4] in ['BACHER', 'MACHER']):
                nxt = ('K', 2)
            # special case 'CAESAR'
            elif pos == first and st[first:first + 6] == 'CAESAR':
                nxt = ('S', 2)
            elif st[pos:pos + 4] == 'CHIA':  # italian 'chianti'
                nxt = ('K', 2)
            elif st[pos:pos + 2] == 'CH':
                # find 'michael'
                if pos > first and st[pos:pos + 4] == 'CHAE':
                    nxt = ('K', 'X', 2)
                elif pos == first and (st[pos + 1:pos + 6] in ['HARAC', 'HARIS'] or \
                   st[pos + 1:pos + 4] in ["HOR", "HYM", "HIA", "HEM"]) and st[first:first + 5] != 'CHORE':
                    nxt = ('K', 2)
                #germanic, greek, or otherwise 'ch' for 'kh' sound
                elif st[first:first + 4] in ['VAN ', 'VON '] or st[first:first + 3] == 'SCH' \
                   or st[pos - 2:pos + 4] in ["ORCHES", "ARCHIT", "ORCHID"] \
                   or st[pos + 2] in ['T', 'S'] \
                   or ((st[pos - 1] in ["A", "O", "U", "E"] or pos == first) \
                   and st[pos + 2] in ["L", "R", "N", "M", "B", "H", "F", "V", "W"]):
                    nxt = ('K', 2)
                else:
                    if pos > first:
                        if st[first:first + 2] == 'MC':
                            nxt = ('K', 2)
                        else:
                            nxt = ('X', 'K', 2)
                    else:
                        nxt = ('X', 2)
            # e.g, 'czerny'
            elif st[pos:pos + 2] == 'CZ' and st[pos - 2:pos + 2] != 'WICZ':
                nxt = ('S', 'X', 2)
            # e.g., 'focaccia'
            elif st[pos + 1:pos + 4] == 'CIA':
                nxt = ('X', 3)
            # double 'C', but not if e.g. 'McClellan'
            elif st[pos:pos + 2] == 'CC' and not (pos == (first + 1) and st[first] == 'M'):
                #'bellocchio' but not 'bacchus'
                if st[pos + 2] in ["I", "E", "H"] and st[pos + 2:pos + 4] != 'HU':
                    # 'accident', 'accede' 'succeed'
                    if (pos == (first + 1) and st[first] == 'A') or \
                       st[pos - 1:pos + 4] in ['UCCEE', 'UCCES']:
                        nxt = ('KS', 3)
                    # 'bacci', 'bertucci', other italian
                    else:
                        nxt = ('X', 3)
                else:
                    nxt = ('K', 2)
            elif st[pos:pos + 2] in ["CK", "CG", "CQ"]:
                nxt = ('K', 2)
            elif st[pos:pos + 2] in ["CI", "CE", "CY"]:
                # italian vs. english
                if st[pos:pos + 3] in ["CIO", "CIE", "CIA"]:
                    nxt = ('S', 'X', 2)
                else:
                    nxt = ('S', 2)
            else:
                # name sent in 'mac caffrey', 'mac gregor
                if st[pos + 1:pos + 3] in [" C", " Q", " G"]:
                    nxt = ('K', 3)
                else:
                    if st[pos + 1] in ["C", "K", "Q"] and st[pos + 1:pos + 3] not in ["CE", "CI"]:
                        nxt = ('K', 2)
                    else:  # default for 'C'
                        nxt = ('K', 1)
        elif ch == u'\xc7':  # will never get here with st.encode('ascii', 'replace') above
            # \xc7 is UTF-8 encoding of Ç
            nxt = ('S', 1)
        elif ch == 'D':
            if st[pos:pos + 2] == 'DG':
                if st[pos + 2] in ['I', 'E', 'Y']:  # e.g. 'edge'
                    nxt = ('J', 3)
                else:
                    nxt = ('TK', 2)
            elif st[pos:pos + 2] in ['DT', 'DD']:
                nxt = ('T', 2)
            else:
                nxt = ('T', 1)
        elif ch == 'F':
            if st[pos + 1] == 'F':
                nxt = ('F', 2)
            else:
                nxt = ('F', 1)
        elif ch == 'G':
            if st[pos + 1] == 'H':
                if pos > first and st[pos - 1] not in vowels:
                    nxt = ('K', 2)
                elif pos < (first + 3):
                    if pos == first:  # 'ghislane', ghiradelli
                        if st[pos + 2] == 'I':
                            nxt = ('J', 2)
                        else:
                            nxt = ('K', 2)
                # Parker's rule (with some further refinements) - e.g., 'hugh'
                elif (pos > (first + 1) and st[pos - 2] in ['B', 'H', 'D']) \
                   or (pos > (first + 2) and st[pos - 3] in ['B', 'H', 'D']) \
                   or (pos > (first + 3) and st[pos - 3] in ['B', 'H']):
                    nxt = (None, 2)
                else:
                    # e.g., 'laugh', 'McLaughlin', 'cough', 'gough', 'rough', 'tough'
                    if pos > (first + 2) and st[pos - 1] == 'U' \
                       and st[pos - 3] in ["C", "G", "L", "R", "T"]:
                        nxt = ('F', 2)
                    else:
                        if pos > first and st[pos - 1] != 'I':
                            nxt = ('K', 2)
            elif st[pos + 1] == 'N':
                if pos == (first + 1) and st[first] in vowels and not is_slavo_germanic:
                    nxt = ('KN', 'N', 2)
                else:
                    # not e.g. 'cagney'
                    if st[pos + 2:pos + 4] != 'EY' and st[pos + 1] != 'Y' and not is_slavo_germanic:
                        nxt = ('N', 'KN', 2)
                    else:
                        nxt = ('KN', 2)
            # 'tagliaro'
            elif st[pos + 1:pos + 3] == 'LI' and not is_slavo_germanic:
                nxt = ('KL', 'L', 2)
            # -ges-,-gep-,-gel-, -gie- at beginning
            elif pos == first and (st[pos + 1] == 'Y' \
               or st[pos + 1:pos + 3] in ["ES", "EP", "EB", "EL", "EY", "IB", "IL", "IN", "IE", "EI", "ER"]):
                nxt = ('K', 'J', 2)
            # -ger-,  -gy-
            elif (st[pos + 1:pos + 3] == 'ER' or st[pos + 1] == 'Y') \
               and st[first:first + 6] not in ["DANGER", "RANGER", "MANGER"] \
               and st[pos - 1] not in ['E', 'I'] and st[pos - 1:pos + 2] not in ['RGY', 'OGY']:
                nxt = ('K', 'J', 2)
            # italian e.g, 'biaggi'
            elif st[pos + 1] in ['E', 'I', 'Y'] or st[pos - 1:pos + 3] in ["AGGI", "OGGI"]:
                # obvious germanic
                if st[first:first + 4] in ['VON ', 'VAN '] or st[first:first + 3] == 'SCH' \
                   or st[pos + 1:pos + 3] == 'ET':
                    nxt = ('K', 2)
                else:
                    # always soft if french ending
                    if st[pos + 1:pos + 5] == 'IER ':
                        nxt = ('J', 2)
                    else:
                        nxt = ('J', 'K', 2)
            elif st[pos + 1] == 'G':
                nxt = ('K', 2)
            else:
                nxt = ('K', 1)
        elif ch == 'H':
            # only keep if first & before vowel or btw. 2 vowels
            if (pos == first or st[pos - 1] in vowels) and st[pos + 1] in vowels:
                nxt = ('H', 2)
            else:  # (also takes care of 'HH')
                nxt = (None, 1)
        elif ch == 'J':
            # obvious spanish, 'jose', 'san jacinto'
            if st[pos:pos + 4] == 'JOSE' or st[first:first + 4] == 'SAN ':
                if (pos == first and st[pos + 4] == ' ') or st[first:first + 4] == 'SAN ':
                    nxt = ('H', )
                else:
                    nxt = ('J', 'H')
            elif pos == first and st[pos:pos + 4] != 'JOSE':
                nxt = ('J', 'A')  # Yankelovich/Jankelowicz
            else:
                # spanish pron. of e.g. 'bajador'
                if st[pos - 1] in vowels and not is_slavo_germanic \
                   and st[pos + 1] in ['A', 'O']:
                    nxt = ('J', 'H')
                else:
                    if pos == last:
                        nxt = ('J', ' ')
                    else:
                        if st[pos + 1] not in ["L", "T", "K", "S", "N", "M", "B", "Z"] \
                           and st[pos - 1] not in ["S", "K", "L"]:
                            nxt = ('J', )
                        else:
                            nxt = (None, )
            if st[pos + 1] == 'J':
                nxt = nxt + (2, )
            else:
                nxt = nxt + (1, )
        elif ch == 'K':
            if st[pos + 1] == 'K':
                nxt = ('K', 2)
            else:
                nxt = ('K', 1)
        elif ch == 'L':
            if st[pos + 1] == 'L':
                # spanish e.g. 'cabrillo', 'gallegos'
                if (pos == (last - 2) and st[pos - 1:pos + 3] in ["ILLO", "ILLA", "ALLE"]) \
                   or ((st[last - 1:last + 1] in ["AS", "OS"] or st[last] in ["A", "O"]) \
                   and st[pos - 1:pos + 3] == 'ALLE'):
                    nxt = ('L', ' ', 2)
                else:
                    nxt = ('L', 2)
            else:
                nxt = ('L', 1)
        elif ch == 'M':
            if (st[pos + 1:pos + 4] == 'UMB' \
               and (pos + 1 == last or st[pos + 2:pos + 4] == 'ER')) \
               or st[pos + 1] == 'M':
                nxt = ('M', 2)
            else:
                nxt = ('M', 1)
        elif ch == 'N':
            if st[pos + 1] == 'N':
                nxt = ('N', 2)
            else:
                nxt = ('N', 1)
        elif ch == u'\xd1':  # UTF-8 encoding of ﾄ
            nxt = ('N', 1)
        elif ch == 'P':
            if st[pos + 1] == 'H':
                nxt = ('F', 2)
            elif st[pos + 1] in ['P', 'B']:  # also account for "campbell", "raspberry"
                nxt = ('P', 2)
            else:
                nxt = ('P', 1)
        elif ch == 'Q':
            if st[pos + 1] == 'Q':
                nxt = ('K', 2)
            else:
                nxt = ('K', 1)
        elif ch == 'R':
            # french e.g. 'rogier', but exclude 'hochmeier'
            if pos == last and not is_slavo_germanic \
               and st[pos - 2:pos] == 'IE' and st[pos - 4:pos - 2] not in ['ME', 'MA']:
                nxt = ('', 'R')
            else:
                nxt = ('R', )
            if st[pos + 1] == 'R':
                nxt = nxt + (2, )
            else:
                nxt = nxt + (1, )
        elif ch == 'S':
            # special cases 'island', 'isle', 'carlisle', 'carlysle'
            if st[pos - 1:pos + 2] in ['ISL', 'YSL']:
                nxt = (None, 1)
            # special case 'sugar-'
            elif pos == first and st[first:first + 5] == 'SUGAR':
                nxt = ('X', 'S', 1)
            elif st[pos:pos + 2] == 'SH':
                # germanic
                if st[pos + 1:pos + 5] in ["HEIM", "HOEK", "HOLM", "HOLZ"]:
                    nxt = ('S', 2)
                else:
                    nxt = ('X', 2)
            # italian & armenian
            elif st[pos:pos + 3] in ["SIO", "SIA"] or st[pos:pos + 4] == 'SIAN':
                if not is_slavo_germanic:
                    nxt = ('S', 'X', 3)
                else:
                    nxt = ('S', 3)
            # german & anglicisations, e.g. 'smith' match 'schmidt', 'snider' match 'schneider'
            # also, -sz- in slavic language altho in hungarian it is pronounced 's'
            elif (pos == first and st[pos + 1] in ["M", "N", "L", "W"]) or st[pos + 1] == 'Z':
                nxt = ('S', 'X')
                if st[pos + 1] == 'Z':
                    nxt = nxt + (2, )
                else:
                    nxt = nxt + (1, )
            elif st[pos:pos + 2] == 'SC':
                # Schlesinger's rule
                if st[pos + 2] == 'H':
                    # dutch origin, e.g. 'school', 'schooner'
                    if st[pos + 3:pos + 5] in ["OO", "ER", "EN", "UY", "ED", "EM"]:
                        # 'schermerhorn', 'schenker'
                        if st[pos + 3:pos + 5] in ['ER', 'EN']:
                            nxt = ('X', 'SK', 3)
                        else:
                            nxt = ('SK', 3)
                    else:
                        if pos == first and st[first + 3] not in vowels and st[first + 3] != 'W':
                            nxt = ('X', 'S', 3)
                        else:
                            nxt = ('X', 3)
                elif st[pos + 2] in ['I', 'E', 'Y']:
                    nxt = ('S', 3)
                else:
                    nxt = ('SK', 3)
            # french e.g. 'resnais', 'artois'
            elif pos == last and st[pos - 2:pos] in ['AI', 'OI']:
                nxt = ('', 'S', 1)
            else:
                nxt = ('S', )
                if st[pos + 1] in ['S', 'Z']:
                    nxt = nxt + (2, )
                else:
                    nxt = nxt + (1, )
        elif ch == 'T':
            if st[pos:pos + 4] == 'TION':
                nxt = ('X', 3)
            elif st[pos:pos + 3] in ['TIA', 'TCH']:
                nxt = ('X', 3)
            elif st[pos:pos + 2] == 'TH' or st[pos:pos + 3] == 'TTH':
                # special case 'thomas', 'thames' or germanic
                if st[pos + 2:pos + 4] in ['OM', 'AM'] or st[first:first + 4] in ['VON ', 'VAN '] \
                   or st[first:first + 3] == 'SCH':
                    nxt = ('T', 2)
                else:
                    nxt = ('0', 'T', 2)
            elif st[pos + 1] in ['T', 'D']:
                nxt = ('T', 2)
            else:
                nxt = ('T', 1)
        elif ch == 'V':
            if st[pos + 1] == 'V':
                nxt = ('F', 2)
            else:
                nxt = ('F', 1)
        elif ch == 'W':
            # can also be in middle of word
            if st[pos:pos + 2] == 'WR':
                nxt = ('R', 2)
            elif pos == first and (st[pos + 1] in vowels or st[pos:pos + 2] == 'WH'):
                # Wasserman should match Vasserman
                if st[pos + 1] in vowels:
                    nxt = ('A', 'F', 1)
                else:
                    nxt = ('A', 1)
            # Arnow should match Arnoff
            elif (pos == last and st[pos - 1] in vowels) \
               or st[pos - 1:pos + 4] in ["EWSKI", "EWSKY", "OWSKI", "OWSKY"] \
               or st[first:first + 3] == 'SCH':
                nxt = ('', 'F', 1)
            # polish e.g. 'filipowicz'
            elif st[pos:pos + 4] in ["WICZ", "WITZ"]:
                nxt = ('TS', 'FX', 4)
            else:  # default is to skip it
                nxt = (None, 1)
        elif ch == 'X':
            # french e.g. breaux
            nxt = (None, )
            if not(pos == last and (st[pos - 3:pos] in ["IAU", "EAU"] \
               or st[pos - 2:pos] in ['AU', 'OU'])):
                nxt = ('KS', )
            if st[pos + 1] in ['C', 'X']:
                nxt = nxt + (2, )
            else:
                nxt = nxt + (1, )
        elif ch == 'Z':
            # chinese pinyin e.g. 'zhao'
            if st[pos + 1] == 'H':
                nxt = ('J', )
            elif st[pos + 1:pos + 3] in ["ZO", "ZI", "ZA"] \
               or (is_slavo_germanic and pos > first and st[pos - 1] != 'T'):
                nxt = ('S', 'TS')
            else:
                nxt = ('S', )
            if st[pos + 1] == 'Z' or st[pos + 1] == 'H':
                nxt = nxt + (2, )
            else:
                nxt = nxt + (1, )
        # ----------------------------------
        # --- end checking letters------
        # ----------------------------------
        #print str(nxt)
        if len(nxt) == 2:
            if nxt[0]:
                pri += nxt[0]
                sec += nxt[0]
            pos += nxt[1]
        elif len(nxt) == 3:
            if nxt[0]:
                pri += nxt[0]
            if nxt[1]:
                sec += nxt[1]
            pos += nxt[2]
    if pri == sec:
        return (pri, '')
    else:
        return (pri, sec)
#}}}

