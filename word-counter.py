#!/usr/bin/env python

import re
import json
import time
import requests
from collections import defaultdict
from sseclient import SSEClient
from bs4 import BeautifulSoup
from redis import StrictRedis
from pymongo import MongoClient


wrap_time = 86400
persian_chars = '\u200cآابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیءؤئأ'
redis = StrictRedis()
all_words = {}
mongo_client = MongoClient()


def normalize_text(text):
    # convert arabic yeh letters to persian yeh
    text = text.replace('\u064a', '\u06cc')
    text = text.replace('\u0649', '\u06cc')
    text = text.replace('\u06d2', '\u06cc')

    # convert heh goal to normal heh
    text = text.replace('\u06c1', '\u0647')

    # convert heh with yeh above to normal heh
    text = text.replace('\u06c0', '\u0647')

    # convert arabic "teh marbuta" to heh
    text = text.replace('\u0629', '\u0647')

    # convert urdu "heh docheshm" to heh
    text = text.replace('\u06be', '\u0647')

    # convert arabic kaf to persian kaf
    text = text.replace('\u0643', '\u06a9')

    # convert arabic with hamzeh below, to an alef
    text = text.replace('\u0625', '\u0627')

    # convert arabic digits to persian digits
    text = text.replace('\u0660', '\u06f0')
    text = text.replace('\u0661', '\u06f1')
    text = text.replace('\u0662', '\u06f2')
    text = text.replace('\u0663', '\u06f3')
    text = text.replace('\u0664', '\u06f4')
    text = text.replace('\u0665', '\u06f5')
    text = text.replace('\u0666', '\u06f6')
    text = text.replace('\u0667', '\u06f7')
    text = text.replace('\u0668', '\u06f8')
    text = text.replace('\u0669', '\u06f9')

    # remove left-to-right and right-to-left marks
    text = text.replace('\u200e', '')
    text = text.replace('\u200f', '')

    # remove short vowels, tanwins, and other characters that go above
    # or below other letters
    text = re.sub('[\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0653\u0654\u0655\u0670]', '', text)

    # remove kashida (U+0640)
    text = text.replace('\u0640', '')

    # remove non-persian characters and replace them with space
    text = ''.join(c if c in persian_chars else ' ' for c in text)

    return text


def normalize_words(words):
    # \xa0 is the "no-break space" character and seems to be
    # occasionally used on its own
    words = [w for w in words if w]

    # remove numbers
    words = [w for w in words if not all(c in '۰۱۲۳۴۵۶۷۸۹' for c in w)]

    return words


def add_post(post):
    global all_words

    html = '<div><div>{}</div><div>{}</div></div>'.format(
        post['title'], post['description'])
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text()
    text = normalize_text(text)

    words = text.split(' ')
    words = normalize_words(words)

    new_words = set(words) - all_words

    with redis.pipeline(transaction=False) as pipe:
        pipe.incr('trends:docs')
        if words:
            pipe.sadd('trends:words', *words)
            all_words.update(words)

        for w in words:
            pipe.incr('trends:' + w + ':count')

        for w in set(words):
            pipe.incr('trends:' + w + ':docs')

        pipe.execute()

    word_counts = [(w, int(redis.get('trends:{}:count'.format(w))))
                   for w in all_words]
    top = list(reversed(sorted(word_counts, key=lambda r: r[1])))[:10]

    print('Doc #{}'.format(int(redis.get('trends:docs'))))
    print('Unique words:', redis.scard('trends:words'))
    print('New words:', ', '.join(new_words))
    print('Top words:', ', '.join(
        '{}={} [docs={}]'
        .format(w, c, int(redis.get('trends:{}:docs'.format(w))))
        for w, c in top))
    print()

    now = int(time.time())
    last_updated = redis.get('trends:lastupdate')
    if last_updated:
        last_updated = int(last_updated)
    else:
        last_updated = now
        redis.set('trends:lastupdate', last_updated)
    if now - last_updated > wrap_time:
        print('Wrapping...')

        words = [
            {
                'word': w,
                'total': int(redis.get('trends:{}:count'.format(w))),
                'docs': int(redis.get('trends:{}:docs'.format(w)))
            }
            for w in all_words
        ]
        doc = {
            'timestamp': now,
            'words': words,
        }

        mongo_client.trends.periods.insert_one(doc)

        wraps = redis.incr('trends:wraps')
        redis.flushdb()
        redis.set('trends:wraps', wraps)
        redis.set('trends:lastupdate', now)
        all_words = set()

        print('Wrap complete.')
        print()


def main():
    global all_words

    url = 'http://forcefeed.ir/sse'

    print('Getting current words...')
    all_words = {w.decode() for w in redis.smembers('trends:words')}

    print('Connecting to forcefeed...')
    client = SSEClient(requests.get(url, stream=True))

    nevents = 0
    try:
        print('Receiving events...')
        for event in client.events():
            add_post(json.loads(event.data))
            nevents += 1
    except KeyboardInterrupt:
        print()

    print('Received {} events.'.format(nevents))

    words = [(w,
              int(redis.get('trends:{}:count'.format(w))),
              int(redis.get('trends:{}:docs'.format(w))))
             for w in all_words]
    top = list(reversed(sorted(words, key=lambda r: r[1])))[:1000]
    print('Top 1000:', ', '.join('{}={},{}'.format(w, total, docs) for w, total, docs in top))


if __name__ == '__main__':
    main()
