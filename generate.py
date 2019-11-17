#!/usr/bin/env python3
#generate.py
'''Generator functions for ids and server numbers'''
import random

_WEIGHTS = [
	  (5, 75), (6, 75), (7, 75), (8, 75), (16, 75), (17, 75)
	, (18, 75), (9, 95), (11, 95), (12, 95), (13, 95), (14, 95)
	, (15, 95), (19, 110), (23, 110), (24, 110), (25, 110)
	, (26, 110), (28, 104), (29, 104), (30, 104), (31, 104)
	, (32, 104), (33, 104), (35, 101), (36, 101), (37, 101)
	, (38, 101), (39, 101), (40, 101), (41, 101), (42, 101)
	, (43, 101), (44, 101), (45, 101), (46, 101), (47, 101)
	, (48, 101), (49, 101), (50, 101), (52, 110), (53, 110)
	, (55, 110), (57, 110), (58, 110), (59, 110), (60, 110)
	, (61, 110), (62, 110), (63, 110), (64, 110), (65, 110)
	, (66, 110), (68, 95), (71, 116), (72, 116), (73, 116)
	, (74, 116), (75, 116), (76, 116), (77, 116), (78, 116)
	, (79, 116), (80, 116), (81, 116), (82, 116), (83, 116)
	, (84, 116)
]
_TOTAL_WEIGHT = sum([n[1] for n in _WEIGHTS])
_SPECIALS = {
	  "de-livechat": 5, "ver-anime": 8, "watch-dragonball": 8, "narutowire": 10
	, "dbzepisodeorg": 10, "animelinkz": 20, "kiiiikiii": 21, "soccerjumbo": 21
	, "vipstand": 21, "cricket365live": 21, "pokemonepisodeorg": 22
	, "watchanimeonn": 22, "leeplarp": 27, "animeultimacom": 34
	, "rgsmotrisport": 51, "cricvid-hitcric-": 51, "tvtvanimefreak": 54
	, "stream2watch3": 56, "mitvcanal": 56, "sport24lt": 56, "ttvsports": 56
	, "eafangames": 56, "myfoxdfw": 67, "peliculas-flv": 69, "narutochatt": 70
}

def session_id() -> str:
	'''Generate unique ID for a group. Might be reset by the server'''
	return str(int(random.randrange(10 ** 15, (10 ** 16) - 1)))

def aid(ncolor: str, group_id: str) -> str:
	'''Generate 4 digit anon ID'''
	try:
		ncolor = ncolor.rsplit('.', 1)[0]
		ncolor = ncolor[-4:]
		int(ncolor)	#insurance that n is int-able
	except ValueError:
		ncolor = "3452"
	return "".join(map(lambda i, v: str((int(i) + int(v)) % 10)
		, ncolor, group_id[4:8]))

def anon_ncolor() -> str:
	'''Random 4 digit 'color' used to calculate anon id'''
	return str(random.randrange(0, 10000)).zfill(4)[:4]

def reverse_aid(goal, group_id) -> str:
	'''Reverse-generate anon ID'''
	return "".join(map(lambda g, v: str((int(g) - int(v)) % 10)
		, goal, group_id[4:8]))

def server_num(group) -> int:
	'''Return server number, or -1 if no server found'''
	#this is black magic I got from ch.py
	if group in _SPECIALS:
		return _SPECIALS[group]
	group = group.replace('-', 'q').replace('_', 'q')
	#we need alnum because of the base-36 conversion
	if not group.isalnum():
		raise ValueError("invalid character in group name")
	temp = max(int(group[6:9], 36), 1000) if len(group) >= 7 else 1000
	max_weight = int(group[:5], 36) % temp

	total = 0
	for ret, weight in _WEIGHTS:
		total += weight*temp / _TOTAL_WEIGHT
		if total >= max_weight:
			return ret
	return -1
