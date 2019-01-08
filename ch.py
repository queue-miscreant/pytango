#!/usr/bin/env python3
#ch.py
'''
An asyncio rewrite of the chatango library based on cellsheet's chlib.py and
lumirayz's ch.py. Event based library for chatango rooms. Features channel 
support and fetching history messages among all other functionalities provided
by those versions.
'''
#TODO	better modtools
#TODO	property docstrings
#TODO	finish implementing PMs
#
#		when receiving "track" commands in private messages, each "track" download
#		is followed like a `getblock` command
#
#		pending checking if `getblock` is "get tracking block" or "get blocklist" (which is null)

################################
#Python Imports
################################
import random
import re
import urllib.request
from urllib.parse import unquote
import asyncio
from os.path import basename
from socket import gaierror
from functools import partial

#enumerable constants
FONT_FACES = [
	  "Arial"
	, "Comic Sans"
	, "Georgia"
	, "Handwriting"
	, "Impact"
	, "Palatino"
	, "Papyrus"
	, "Times New Roman"
	, "Typewriter"
]
#limited sizes available for non-premium accounts
FONT_SIZES = [9, 10, 11, 12, 13, 14]
CHANNEL_NAMES = ["None", "Red", "Blue", "Both"]

BIGMESSAGE_CUT = 0
BIGMESSAGE_MULTIPLE = 1

POST_TAG_RE = re.compile("(<n([a-fA-F0-9]{1,6})\\/>)?" + \
	"(<f x([0-9a-fA-F]{2,8})=\"([0-9a-zA-Z]*)\">)?")
XML_TAG_RE = re.compile("(</?(.*?)/?>)")
THUMBNAIL_FIX_RE = re.compile(r"(https?://ust\.chatango\.com/.+?/)t(_\d+.\w+)")

WEIGHTS = [['5', 75], ['6', 75], ['7', 75], ['8', 75], ['16', 75], ['17', 75], ['18', 75], ['9', 95], ['11', 95], ['12', 95], ['13', 95], ['14', 95], ['15', 95], ['19', 110], ['23', 110], ['24', 110], ['25', 110], ['26', 110], ['28', 104], ['29', 104], ['30', 104], ['31', 104], ['32', 104], ['33', 104], ['35', 101], ['36', 101], ['37', 101], ['38', 101], ['39', 101], ['40', 101], ['41', 101], ['42', 101], ['43', 101], ['44', 101], ['45', 101], ['46', 101], ['47', 101], ['48', 101], ['49', 101], ['50', 101], ['52', 110], ['53', 110], ['55', 110], ['57', 110], ['58', 110], ['59', 110], ['60', 110], ['61', 110], ['62', 110], ['63', 110], ['64', 110], ['65', 110], ['66', 110], ['68', 95], ['71', 116], ['72', 116], ['73', 116], ['74', 116], ['75', 116], ['76', 116], ['77', 116], ['78', 116], ['79', 116], ['80', 116], ['81', 116], ['82', 116], ['83', 116], ['84', 116]]
SPECIALS = {"de-livechat": 5, "ver-anime": 8, "watch-dragonball": 8, "narutowire": 10, "dbzepisodeorg": 10, "animelinkz": 20, "kiiiikiii": 21, "soccerjumbo": 21, "vipstand": 21, "cricket365live": 21, "pokemonepisodeorg": 22, "watchanimeonn": 22, "leeplarp": 27, "animeultimacom": 34, "rgsmotrisport": 51, "cricvid-hitcric-": 51, "tvtvanimefreak": 54, "stream2watch3": 56, "mitvcanal": 56, "sport24lt": 56, "ttvsports": 56, "eafangames": 56, "myfoxdfw": 67, "peliculas-flv": 69, "narutochatt": 70}

_HTML_CODES = [
	  ("&#39;", "'")
	, ("&gt;", '>')
	, ("&lt;", '<')
	, ("&quot;", '"')
	, ("&apos;", "'")
	, ("&amp;", '&')
]
def format_raw(raw):
	'''
	Format a raw html string into one with newlines
	instead of <br>s and all tags formatted out
	'''
	if not raw:
		return raw
	#replace <br>s with actual line breaks
	#otherwise, remove html
	acc = 0
	for i in XML_TAG_RE.finditer(raw):
		start, end = i.span(1)
		rep = ""
		if i.group(2) == "br":
			rep = '\n'
		raw = raw[:start-acc] + rep + raw[end-acc:]
		acc += end-start - len(rep)
	raw.replace("&nbsp;", ' ')
	for i, j in _HTML_CODES:
		raw = raw.replace(i, j)
	#remove trailing \n's
	while raw and raw[-1] == "\n":
		raw = raw[:-1]
	#thumbnail fix in chatango
	raw = THUMBNAIL_FIX_RE.subn(r"\1l\2", raw)[0]
	return raw

class Post:
	'''
	Objects that represent messages in chatango
	Post objects have support for channels and formatting parsing
	'''
	def __init__(self, raw, msgtype):
		if msgtype == 2:
			self.user = raw[0]
			self.time = float(raw[3])
			self.post = format_raw(':'.join(raw[5:]))
			self.n_color = ''
			self.f_color = ''
			self.f_size = 12
			self.f_face = 0
			return

		self.time  = float(raw[0])
		self.uid   = raw[3]
		self.unid  = raw[4]
		self.pnum  = raw[5] if msgtype == 0 else None
		self.msgid = raw[5] if msgtype == 1 else None
		self.ip    = raw[6]
		self.post  = format_raw(':'.join(raw[9:]))
		#formatting parsing
		self.n_color, self.f_color, self.f_size, self.f_face = \
			self.parse_formatting(raw[9])

		#user parsing
		user = raw[1]
		if not user:
			if raw[2]: #temp name
				user = '#' + raw[2]
			else:
				user = "!anon" + _Generate.aid(self.n_color, self.uid)
			#n_color doesn't count for anons, because it changes their number
			self.n_color = ''

		self.user = user
		channels_and_badge = int(raw[7])
		#magic that turns no badge into 0, mod badge into 1, and staff badge into 2
		self.badge = (channels_and_badge >> 5) & 3
		#magic that turns no channel into 0, red into 1, blue into 2, both into 3
		channel = (channels_and_badge >> 8) & 31
		self.channel = channel&1|((channel&8)>>2)|((channel&16)>>3)

	@staticmethod
	def parse_formatting(message):
		n_color, f_color, f_size, f_face = '', '', 12, 0
		tag = POST_TAG_RE.search(message)
		if tag:
			n_color = tag.group(2) or ''
			size_color = tag.group(4)
			if size_color:
				if len(size_color) % 3 == 2:	#color and font size
					f_size = int(size_color[:2])
					f_color = size_color[2:]
				else:
					f_color = size_color
					f_size = 12
			else:
				f_color = ''
				f_size = 12
			f_face = int(tag.group(5) or 0)
		return n_color, f_color, f_size, f_face

class _Generate:
	'''Generator functions for ids and server numbers'''
	@staticmethod
	def uid():
		'''Generate user ID'''
		return str(int(random.randrange(10 ** 15, (10 ** 16) - 1)))

	@staticmethod
	def aid(n, uid):
		'''Generate anon ID'''
		try:
			n = n.rsplit('.', 1)[0]
			n = n[-4:]
			int(n)	#insurance that n is int-able
		except ValueError:
			n = "3452"
		return "".join(map(lambda i, v: str((int(i) + int(v)) % 10)
			, n, uid[4:8]))

	@staticmethod
	def reverse_aid(goal, uid):
		'''Reverse-generate anon ID'''
		return "".join(map(lambda g, v: str((int(g) - int(v)) % 10)
			, goal, uid[4:8]))

	@staticmethod
	def server_num(group):
		'''Return server number'''
		if group in SPECIALS.keys():
			return SPECIALS[group]
		group = re.sub("-|_", 'q', group)
		wt, gw = sum([n[1] for n in WEIGHTS]), 0
		try:
			num1 = 1000 if len(group) < 7 else max(int(group[6:9], 36), 1000)
			num2 = (int(group[:5], 36) % num1) / num1
		except ValueError:
			return
		for i, j in WEIGHTS:
			gw += j / wt
			if gw >= num2:
				return i

	@staticmethod
	def pm_auth(username, password):
		'''Request auth cookie for PMs'''
		data = urllib.parse.urlencode({
			  "user_id":		username
			, "password":		password
			, "storecookie":	"on"
			, "checkerrors":	"yes"
		}).encode()

		login = urllib.request.urlopen("http://chatango.com/login", data=data)
		for i in login.headers.get_all("Set-Cookie"):
			search = re.search("auth.chatango.com=(.*?);", i)
			if search:
				return search.group(1)

class _Multipart(urllib.request.Request):
	'''Simplified version of requests.post for multipart/form-data'''
	#code adapted from http://code.activestate.com/recipes/146306/
	_MULTI_BOUNDARY = '---------------iM-in-Ur-pr07oc01'
	_DISPOSITION = "Content-Disposition: form-data; name=\"%s\""

	def __init__(self, url, data, headers={}):
		multiform = []
		for i, j in data.items():
			multiform.append("--" + self._MULTI_BOUNDARY) #add boundary
			data = j
			#the next part can have a (mime type, file) tuple
			if isinstance(j, (tuple, list)):
				if len(j) != 2:
					raise ValueError("improper multipart file tuple formatting")
				try:
					#try to read the file first
					data = j[1].read()
					#then set the filename to filename
					multiform.append((self._DISPOSITION % i) + \
						"; filename=\"%s\"" % basename(j[1].name))
					multiform.append("Content-Type: %s" % j[0])
				except AttributeError as exc:
					raise ValueError("expected file-like object") from exc
			else:
				#no mime type supplied
				multiform.append(self._DISPOSITION % j)
			multiform.append("")
			multiform.append(data)
		multiform.append("--" + self._MULTI_BOUNDARY + "--")
		#encode multiform
		request_body = (b"\r\n").join([isinstance(i, bytes) and i or i.encode() \
			for i in multiform])

		headers.update({
			  "content-length":	str(len(request_body))
			, "content-type":	"multipart/form-data; boundary=%s"%\
				self._MULTI_BOUNDARY
		})
		super().__init__(url, data=request_body, headers=headers)

class ChatangoProtocol(asyncio.Protocol):
	'''Virtual class interpreting chatango's protocol'''
	_ping_delay = 15
	_longest_ping = 60
	def __init__(self, manager, storage, loop=None):
		self._loop = manager.loop if loop is None else loop
		self._storage = storage
		self._manager = manager
		self._ping_task = None
		#socket stuff
		self._transport = None
		self._last_command = -1
		self.connected = False
		self._rbuff = b""
		#user id
		self._uid = _Generate.uid()

	#########################################
	#	Callbacks
	#########################################

	def data_received(self, data):
		'''Parse argument as data from the socket and call method'''
		self._rbuff += data
		commands = self._rbuff.split(b'\x00')
		for command in commands[:-1]:
			args = command.decode('utf-8').rstrip("\r\n").split(':')
			try:
				#create a task for the recv event
				print(args)
				receive = getattr(self, "_recv_"+args[0])
				self._loop.create_task(receive(args[1:]))
			except AttributeError:
				pass
		self._rbuff = commands[-1]
		self._last_command = self._loop.time()

	def connection_lost(self, exc):
		'''Cancel the ping task and fire on_connection_error'''
		if self._ping_task:
			self._ping_task.cancel()
		if self.connected: #connection lost if the transport closes abruptly
			self._call_event("on_connection_error", exc)
	#########################################

	def send_command(self, *args, firstcmd=False):
		'''Send data to socket'''
		if self._transport is None:
			return
		if firstcmd:
			self._transport.write(bytes(':'.join(args)+'\x00', "utf-8"))
		else:
			self._transport.write(bytes(':'.join(args)+'\r\n\x00', "utf-8"))

	def _call_event(self, event, *args, **kw):
		'''Attempt to call manager's method'''
		try:
			event = getattr(self._manager, event)
			self._loop.create_task(event(self._storage, *args, **kw))
		except AttributeError:
			pass

	async def disconnect(self, raise_error=False):
		'''Safely close the transport. Prevents firing on_connection_error 'lost' '''
		if self._transport is not None:
			self._transport.close()
		#cancel the ping task now
		if self._ping_task is not None:
			self._ping_task.cancel()
			self._ping_task = None
		self.connected = raise_error

	async def ping(self):
		'''Send a ping to keep the transport alive'''
		while self._loop.time() - self._last_command < self._longest_ping:
			self.send_command("")
			await asyncio.sleep(self._ping_delay)
		self._transport.close()

class GroupProtocol(ChatangoProtocol):
	'''Protocol interpreter for Chatango group commands'''
	def __init__(self, room, manager, loop=None):
		super().__init__(manager, Group(self, room), loop=loop)
		#intermediate message stuff and aux data for commands
		self._messages = {}
		self._updates = {}
		self._history = []
		self._last_message = 0

	def connection_made(self, transport):
		'''Begins communication with the server and connects to the room'''
		#if i cared, i'd put this property-setting in a super method
		self._transport = transport
		self.connected = True
		self._last_command = self._loop.time()
		self.send_command("bauth", self._storage._name, self._uid, self._manager.username,
			self._manager.password, firstcmd=True)

	#--------------------------------------------------------------------------
	async def _recv_ok(self, args):
		'''ACK that login succeeded'''
		if args[2] == 'C':
			if (not self._manager.password) and (not self._manager.username):
				aid = self._storage._aid
				if aid is not None:
					ncolor = _Generate.reverse_aid(aid, args[1])
				else:
					ncolor = str(random.randrange(0, 10000)).zfill(4)[:4]
				self._storage._aid = _Generate.aid(ncolor, args[1])
				self._storage._nColor = ncolor
			elif not self._manager.password:
				self.send_command("blogin", self._manager.username)
			else:
				self._call_event("on_login_fail")
				await self.disconnect()
				return
		else:
			self._storage._aid = None
		#shouldn't be necessary, but if the room assigns us a new id
		self._uid = args[1]
		self._storage._owner = args[0]
		self._storage._mods = set(mod.split(',')[0].lower()
			for mod in args[6].split(';'))
		#create a ping
		self._ping_task = self._loop.create_task(self.ping())

	async def _recv_denied(self, _):
		'''NACK that no such server exists'''
		self._call_event("on_denied")
		await self.disconnect()

	async def _recv_inited(self, _):
		'''Room inited, after recent messages have sent'''
		self.send_command("gparticipants")		#open up feed for members joining/leaving
		self.send_command("getpremium", '1')	#try to turn on premium features
		self.send_command("getbannedwords")		#what it says on the tin
		self.send_command("getratelimit")		#get posts allowed per n seconds
		self._call_event("on_connect")
		self._call_event("on_history_done", self._history.copy()) #clone history
		self._history.clear()

	async def _recv_badalias(self, _):
		'''NACK to blogin. Has corresponding ACK, but does nothing'''
		self._call_event("on_bad_alias")

	async def _recv_gparticipants(self, args):
		'''Command that contains information of current room members'''
		#gparticipants splits people by ;
		people = ':'.join(args[1:]).split(';')
		#room is empty except anons
		if people != ['']:
			for person in people:
				person = person.split(':')
				if person[3] != "None" and person[4] == "None":
					self._storage._users.append(person[3].lower())
		self._call_event("on_participants")

	async def _recv_participant(self, args):
		'''New member joined or left'''
		bit = args[0]
		if bit == '0':	#left
			user = args[3].lower()
			if args[3] != "None" and user in self._storage._users:
				self._storage._users.remove(user)
				self._call_event("on_member_leave", user)
			else:
				self._call_event("on_member_leave", "anon")
		elif bit == '1':	#joined
			user = args[3].lower()
			if args[3] != "None":
				self._storage._users.append(user)
				self._call_event("on_member_join", user)
			else:
				self._call_event("on_member_join", "anon")
		elif bit == '2':	#tempname blogins
			user = args[4].lower()
			self._call_event("on_member_join", user)

	async def _recv_bw(self, args):
		'''Banned words'''
		parts = unquote(args[0])
		words = unquote(args[1])
		self._storage._banned_parts = parts.split(',')
		self._storage._banned_words = words.split(',')

	async def _recv_n(self, args):
		'''Number of users, in base 16'''
		self._storage._usercount = int(args[0], 16)
		self._call_event("on_usercount")

	async def _recv_b(self, args):
		'''Message received'''
		post = Post(args, 0)
		if post.time > self._last_message:
			self._last_message = post.time
		if post.pnum in self._updates:
			post.msgid = self._updates[post.pnum]
			self._call_event("on_message", post)
			del self._updates[post.pnum]
		else: #wait for push by update message
			self._messages[post.pnum] = post

	async def _recv_u(self, args):
		'''Message updated'''
		post = self._messages.get(args[0])
		if post is not None:
			del self._messages[args[0]]
			post.msgid = args[1]
			self._call_event("on_message", post)
		else:
			self._updates[args[0]] = args[1]

	async def _recv_i(self, args):
		'''Historical message'''
		post = Post(args, 1)
		if post.time > self._last_message:
			self._last_message = post.time
		self._history.append(post)

	async def _recv_gotmore(self, _):
		'''Received all historical messages'''
		self._call_event("on_history_done", list(self._history))
		self._history.clear()
		self._storage._history_count += 1

	async def _recv_nomore(self, _):
		self._storage._no_more = True
		self._call_event("on_no_more_messges")

	async def _recv_show_fw(self, _):
		'''Flood warning'''
		self._call_event("on_flood_warning")

	async def _recv_show_tb(self, args):
		'''Flood ban'''
		self._call_event("on_flood_ban", int(args[0]))

	async def _recv_tb(self, args):
		'''Flood ban reminder'''
		self._call_event("on_flood_ban_repeat", int(args[0]))

	async def _recv_blocklist(self, args):
		'''Received list of banned users'''
		self._storage._banlist.clear()
		sections = ':'.join(args).split(';')
		for section in sections:
			params = section.split(':')
			if len(params) != 5:
				continue
			if params[2] == "":
				continue
			self._storage._banlist.append((
				  params[0]	#unid
				, params[1]	#ip
				, params[2]	#target
				, float(params[3]) #time
				, params[4]	#src
			))
		self._call_event("on_banlist_update")

	async def _recv_blocked(self, args):
		'''User banned'''
		if args[2] == "":
			return
		target = args[2]
		user = args[3]
		self._storage._banlist.append((
			  args[0]	#unid
			, args[1]	#ip
			, target
			, float(args[3]) #time
			, user
		))
		self._call_event("on_ban", user, target)
		self.request_banlist()

	async def _recv_unblocked(self, args):
		'''User unbanned'''
		if args[2] == "":
			return
		target = args[2]
		user = args[3]
		self._call_event("on_unban", user, target)
		self.request_banlist()

	async def _recv_mods(self, args):
		'''Moderators changed'''
		mods = set(map(lambda x: x.lower(), args))
		premods = self._storage._mods
		for user in mods - premods: #modded
			self._storage._mods.add(user)
			self._call_event("on_mod_add", user)
		for user in premods - mods: #demodded
			self._storage._mods.remove(user)
			self._call_event("on_mod_remove", user)
		self._call_event("on_mod_change")

	async def _recv_delete(self, args):
		'''Message deleted'''
		self._call_event("on_message_delete", args[0])

	async def _recv_deleteall(self, args):
		'''Message delete (multiple)'''
		for msgid in args:
			self._call_event("on_message_delete", msgid)
	#--------------------------------------------------------------------------
	def request_banlist(self):
		'''Request updated banlist (Mod)'''
		self.send_command("blocklist", "block", "", "next", "500")

class PMProtocol(ChatangoProtocol):
	'''Protocol interpreter for Chatango private message commands'''
	def __init__(self, manager, authkey, loop=None):
		super().__init__(manager, Privates(self), loop=loop)
		self.auth_key = authkey

	def connection_made(self, transport):
		'''Begins communication with and connects to the PM server'''
		#if i cared, i'd put this property-setting in a super method
		self._transport = transport
		self.connected = True
		self._last_command = self._loop.time()
		self.send_command("tlogin", self.auth_key, self._uid, firstcmd=True)

	async def _recv_seller_name(self, *args):
		#seller_name returns two arguments: the session id called with tlogin and
		#the username; neither of these are important except as a sanity check
		pass

	async def _recv_OK(self, args):
		self._call_event("on_pm_connect")
		self.send_command("settings")
		self.send_send_cnd("wl")	#friends list
		self._ping_task = self._loop.create_task(self.ping())

	async def _recv_msg(self, args):
		post = Post(args, 2)
		self._call_event("on_pm", post, False)

	async def _recv_msgoff(self, args):
		post = Post(args, 2)
		self._call_event("on_pm", post, True)

	async def _recv_wl(self, args):
		'''Received friends list (watch list)'''
		self._storage._watch = {}
		it = iter(args)
		try:
			for i in it:
				#username
				self._storage._watch[i] = (
					  float(next(it))			#last message
					, next(it))					#online/offline/app
				next(it)						#lagging 0
		except StopIteration:
			pass
		self._call_event("on_watchlist")

	async def _recv_track(self, args):
		'''Received tracked user'''
		#0: username
		#1: time last online
		#2: online/offline/app
		track = self._storage._track
		track[args[0]] = (float(args[1]), args[2])
		self._call_event("on_track")

	async def _recv_connect(self, args):
		'''Received check online'''
		#TODO
		#0:	username
		#1:	last message time
		#2:	online/offline/app/invalid (not a real person or is a group)

	async def _recv_wladd(self, args):
		'''Received addition to watch list'''
		#0:	username
		#1:	online/offline/app
		#2:	last message time
		self._storage._watch[args[0]] = (
			  float(args[2])
			, args[1])
		self._call_event("on_watchlist_update")

	async def _recv_wldelete(self,args):
		'''Received deletion from watch list'''
		#0:	username
		#1:	'deleted'
		#2:	0
		if self._storage._watch.get(args[0]):
			del self._storage._watch[args[0]]
		self._call_event("on_watchlist_update")

	async def _recv_status(self, args):
		'''Received status update'''
		#0: username
		#1: last time online
		#2:	online/offline/app
		#update watch
		if self._storage._watch.get(args[0]):
			self._storage._watch[args[0]] = (
				  float(args[1])
				, args[2])
			self._call_event("on_watchlist_update")
		#update track
		if self._storage._track.get(args[0]):
			self._storage._track[args[0]] = (
				  float(args[1])
				, args[2])
			self._call_event("on_track")

class Connection:
	'''
	Base class that contains downloaded data and abstracts actions on protocols
	'''
	def __init__(self, protocol):
		self._protocol = protocol

		#account information
		self._aid = None
		self._premium = False
		self._message_bg = False
		self._message_record = False

		#formatting
		self._nColor = None
		self._fSize  = 11
		self._fColor = ""
		self._fFace  = 0

	####################################
	# Properties
	####################################

	nColor = property(lambda self: self._nColor)
	fColor = property(lambda self: self._fColor)
	fSize = property(lambda self: self._fSize)
	fFace = property(lambda self: self._fFace)

	@nColor.setter
	def nColor(self, arg):
		if self._aid is None:
			self._nColor = arg

	@fColor.setter
	def fColor(self, arg):
		self._fColor = arg

	@fSize.setter
	def fSize(self, arg):
		self._fSize = min(22, max(9, int(arg)))

	@fFace.setter
	def fFace(self, arg):
		self._fFace = arg

class Group(Connection):
	'''Class for high-level group communication and storing group information'''
	_MAX_LENGTH = 2000
	_TOO_BIG_MESSAGE = BIGMESSAGE_MULTIPLE
	_ADMIN_FLAGS = 354300
	_MOD_FLAGS = 82368
	def __init__(self, protocol, room):
		super().__init__(protocol)
		#user information
		self._name = room
		self._owner = None
		self._mods = set()
		self._banned_parts = []
		self._banned_words = []
		self._banlist = []
		self._users = []
		self._usercount = 0

		self._history_count = 0
		self._no_more = False

		#########################################
		#	Properties
		#########################################

	@property
	def username(self):
		if self._aid is not None:
			return "!anon" + str(self._aid)
		ret = self._protocol._manager.username
		if not self._protocol._manager.password:
			ret = '#' + ret
		return ret
	name = property(lambda self: self._name)
	owner = property(lambda self: self._owner)
	modlist = property(lambda self: set(self._mods))	#cloned set
	userlist = property(lambda self: list(self._users))	#cloned list
	usercount = property(lambda self: self._usercount)
	banlist = property(lambda self: [banned[2] 
		for banned in self._banlist])					#by name; cloned
	banned_words = property(lambda self: (self._banned_words
		, self._banned_parts))
	last_message = property(lambda self: \
		self._protocol._last_message) #this is nice for the user to access

	def send_post(self, post, channel=0, html=False):
		'''Send a post to the group'''
		if not post:
			return
		channel = (((channel&2)<<2 | (channel&1))<<8)
		if not html:
			#replace HTML equivalents
			for i, j in reversed(_HTML_CODES):
				post = post.replace(j, i)
			post = post.replace('\n', "<br/>")
		if len(post) > self._MAX_LENGTH:
			if self._TOO_BIG_MESSAGE == BIGMESSAGE_CUT:
				self.send_post(post[:self._MAX_LENGTH], channel=channel, html=True)
			elif self._TOO_BIG_MESSAGE == BIGMESSAGE_MULTIPLE:
				while post:
					sect = post[:self._MAX_LENGTH]
					post = post[self._MAX_LENGTH:]
					self.send_post(sect, channel, html=True)
			return
		self._protocol.send_command("bm", "meme", str(channel)
			, "<n{}/><f x{:02d}{}=\"{}\">{}".format(self.nColor, self.fSize
			, self.fColor, self.fFace, post))

	def get_more(self, amt=20):
		'''Get more historical messages'''
		if not self._no_more:
			self._protocol.send_command("get_more", str(amt)
				, str(self._history_count))

	def delete(self, message):
		'''
		Delete a message (Mod)
		Argument `message` must be a `Post` object
		'''
		if self.get_level(self.username) > 0:
			self._protocol.send_command("delmsg", message.msgid)

	def clear_user(self, message):
		'''
		Delete all of a user's messages (Mod)
		Argument `message` must be a `Post` object
		'''
		if self.get_level(self.username) > 0:
			self._protocol.send_command("delallmsg", message.unid)

	def ban(self, message):
		'''
		Ban a user from a message (Mod)
		Argument `message` must be a `Post` object
		'''
		if self.get_level(self.username) > 0:
			self._protocol.send_command("block", message.user, message.ip, message.unid)

	def unban(self, user):
		'''
		Unban a user by name (Mod)
		Argument `user` must be a string
		'''
		rec = None
		for record in self._banlist:
			if record[2] == user:
				rec = record
				break
		if rec:
			self._protocol.send_command("removeblock", rec[0], rec[1], rec[2])
			return True
		return False

	def add_mod(self, user):
		'''Add moderator (Owner)'''
		if self.get_level(self.protocol._manager.username) == 2:
			self._protocol.send_command("addmod", user, self._MOD_FLAGS)

	def remove_mod(self, user):
		'''Remove moderator (Owner)'''
		if self.get_level(self.protocol._manager.username) == 2:
			self._protocol.send_command("removemod", user)

	def clearall(self):
		'''Clear all messages (Owner)'''
		if self.get_level(self.username) == 2:
			self._protocol.send_command("clearall")

	def get_level(self, user):
		'''Get level of permissions in group'''
		if user == self._owner:
			return 2
		if user in self._mods:
			return 1
		return 0

	def set_anon(self, id_number):
		'''Set anon ID to 4 digit number `id_number`'''
		if self._nColor is not None:
			self._nColor = _Generate.reverse_aid(id_number, self._aid)
		else:
			self._aid = str(int(id_number) % 10000).zfill(4)

class Privates(Connection):
	'''
	Class representing high-level private message communication and storage
	'''
	def __init__(self, protocol):
		super().__init__(protocol)

		self._watch = {}	#analogous to a friends list, but not mutual
		self._track = {}	#dict of users to whom `track` has been issued

	def send_post(self, user, post, html=False):
		if not html:
			#replace HTML equivalents
			for i, j in reversed(_HTML_CODES):
				post = post.replace(j, i)
			post = post.replace('\n', "<br/>")
		self._protocol.send_command("msg", user, "<m>{}</m>".format(post))

def _connection_lost_handler(loop, context):
	failed_protocol = context.get("protocol")
	if isinstance(failed_protocol, ChatangoProtocol):
		loop.create_task(failed_protocol.disconnect(True))
	else:
		loop.default_exception_handler(context)

class Manager:
	'''
	Creates and manages connections to chatango.
	Also propogates events from joined groups
	'''
	def __init__(self, username, password, pm=False, loop=None):
		self.loop = asyncio.get_event_loop() if loop is None else loop
		self._groups = []
		self.pm = None
		if pm:
			self.loop.create_task(self.join_pm())
		self.username = username
		self.password = password

		loop.set_exception_handler(_connection_lost_handler)

	def __del__(self):
		if self.loop.is_closed():
			return
		for i in self._groups:
			#disconnect (and cancel all ping tasks)
			self.loop.run_until_complete(i._protocol.disconnect())
		if self.pm is not None:
			self.pm._protocol.disconnect()

	@classmethod
	def add_event(cls, eventname, func):
		'''
		Add an event handler.
		`func` should have a keyword arg `ancestor`, the previous event handler
		'''
		ancestor = None
		#limit modifiable attributes
		if not eventname.startswith("on"):
			raise ValueError("eventname must start with 'on'")
		try:
			ancestor = getattr(cls, eventname)
		except AttributeError:
			pass
		#should be a partially applied function with
		#the event ancestor (a coroutine generator)
		setattr(cls, eventname, partial(func, ancestor=ancestor))

	async def join_group(self, group_name, aid=None, port=443):
		'''Join group `group_name`'''
		group_name = group_name.lower()

		server = _Generate.server_num(group_name)
		if server is None:
			raise ValueError("malformed room token " + group_name)

		#already joined group
		if group_name != self.username and group_name not in self._groups:
			try:
				ret = GroupProtocol(group_name, self)
				if aid is not None:
					ret._storage.set_anon(aid)
				await self.loop.create_connection(lambda: ret,
					"s{}.chatango.com".format(server), port)
				self._groups.append(ret._storage)
				return ret._storage
			except gaierror as exc:
				raise ConnectionError("could not connect to group server") \
				from exc
		elif group_name == self.username:
			return await self.join_pm()
		else:
			raise ValueError("attempted to join group multiple times")

	async def leave_group(self, group_name):
		'''Leave group `group_name`'''
		if isinstance(group_name, Group):
			group_name = group_name.name
		for index, group in enumerate(self._groups):
			if group.name == group_name:
				await group._protocol.disconnect()
				self._groups.pop(index)

	async def join_pm(self, port=5222):
		'''Log into private messages and return Connection'''
		if self.pm is None:
			try:
				authkey = _Generate.pm_auth(self.username, self.password)
				ret = PMProtocol(self, authkey)
				await self.loop.create_connection(lambda: ret,
					"c1.chatango.com", port)
				self.pm = ret._storage
			except gaierror as exc:
				raise ConnectionError("could not connect to PM server") from exc
		return self.pm

	async def leave_pm(self):
		'''If logged into private messages, log out'''
		if self.pm is not None:
			await self.pm._protocol.disconnect()
			self.pm = None

	async def leave_all(self):
		'''Disconnect from all groups and PMs'''
		for group in self._groups:
			await group._protocol.disconnect()
		self._groups.clear()
		await self.leave_pm()

	def upload_avatar(self, location):
		'''Upload an avatar with path `location`'''
		extension = location[location.rfind('.')+1:].lower()
		if extension == "jpg":
			extension = "jpeg"
		elif extension not in ("png", "jpeg"):
			return False

		with open(location, "br") as loc:
			urllib.request.urlopen(_Multipart('http://chatango.com/updateprofile',
				data={
					  'u':			self.username
					, 'p':			self.password
					, "auth":		"pwd"
					, "arch":		"h5"
					, "src":		"group"
					, "action":		"fullpic"
					, "Filedata":	("image/%s" % extension, loc)
				}))
		return True
