#!/usr/bin/env python3
#group.py
'''
Objects representing Groups and Group connections in Chatango.
Implements an asyncio-compatible protocol and provides classes for parsing raw
group data like users, bans, and moderator actions.
'''
import json
import html
import asyncio
from urllib import parse

from . import base, generate
from .post import Post

BIGMESSAGE_CUT = 0
BIGMESSAGE_MULTIPLE = 1

class User:
	'''
	User in a particular Group. Contains all clients (i.e. browser tabs) the
	user belongs to and moderator flags.
	'''
	#TODO? args[2] in participants and gparticipants (for individual users)
	#	contains the first 8 digits of the session ID; it is consistent across
	#	browser tabs and usernames, but not between browsers
	_AVATAR_URL = "http://fp.chatango.com/profileimg/{}/{}/{}/full.jpg"
	def __init__(self, group, name: str, unid=None, join_time=None, mod_flags=0):
		self._name = name
		self._group = group
		self._clients = {}
		if unid is not None and join_time is not None:
			self.new_client(unid, join_time)
		self._mod_flags = ModFlags(mod_flags)

	name = property(lambda self: self._name
		, doc="Display name")
	group = property(lambda self: self._group
		, doc="Group the User belongs to")
	clients = property(lambda self: self._clients.copy()
		, doc="Dict whose keys are client IDs and values are join times")
	join_time = property(lambda self: min(self._clients.values()) \
			if self._clients else 0
		, doc="Float representing earliest join time")
	joined = property(lambda self: bool(self._clients)
		, doc = "Whether the user currently exists within the group")
	mod_flags = property(lambda self: self._mod_flags
		, doc="ModFlags containing moderator permissions")
	@property
	def avatar(self):
		'''A url that points to the user's avatar image'''
		name = self._name.lower()
		ch0 = name[0]
		ch1 = ch0
		if len(name) > 1:
			ch1 = name[1]
		return self._AVATAR_URL.format(ch0, ch1, name)

	def __repr__(self):
		return "{}({}, {})".format(type(self).__name__
			, repr(self._name), set(self._clients))

	def __str__(self):
		return self._name

	def __format__(self, _):
		ret = self._name
		if len(self._clients) > 1:
			ret += " ({})".format(len(self._clients))
		return ret

	#for sets
	def __hash__(self):
		return hash(self._name.lower())

	def __eq__(self, other):
		if isinstance(other, type(self)):
			return self._name.lower() == other.name.lower()
		if isinstance(other, str):
			return self._name.lower() == other.lower()
		return False

	@classmethod
	def init_mod(cls, group, args):
		for user in group._users:
			if user == args[0]:
				user.promote(args[1])
				return user
		return cls(group, args[0], mod_flags=args[1])

	@classmethod
	def init_participant(cls, group, args):
		'''
		Initializer for instance based on `participant` args.
		Returns a 2-tuple of appropriate User (or str) and and bool signifying:
		False:	User left
		True:	User joined
		'''
		joined = int(args[0])
		username = args[3]
		#handle moderators changed
		for mod in group.mods:
			if mod == username:
				if joined:
					mod.new_client(args[1], args[6])
				else:
					mod.remove_client(args[1])
				return mod, bool(joined)
			#user logout occurred
			if joined == 2 and int(args[1]) in mod.clients:
				mod.remove_client(args[1])
				return mod, False
		#handle user changed
		for user in group._users:
			if user == username:
				if joined:
					user.new_client(args[1], args[6])
				else:
					user.remove_client(args[1])
				return user, bool(joined)
			#user logout occurred
			if joined == 2 and int(args[1]) in user.clients:
				user.remove_client(args[1])
				return user, False
		#anon or user name setting occurred
		if username == "None":
			if joined == 2:
				username = args[4]
			else:
				username = "anon"
			return username, True
		return cls(group, username, unid=args[1], join_time=args[6]), True

	@classmethod
	def init_g_participant(cls, group, args):
		username = args[3]
		if username == "None":
			return None
		#handle moderators changed
		for mod in group.mods:
			if mod == username:
				mod.new_client(args[0], args[1])
				return mod
		#handle user changed
		for user in group.users:
			if user == username:
				user.new_client(args[0], args[1])
				return user
		return cls(group, args[3], unid=args[0], join_time=args[1])

	def promote(self, flags):
		'''Set the mod flags. Used internally when mods are promoted/demoted'''
		self._mod_flags = ModFlags(int(flags))

	def remove_client(self, unid):
		'''Remove entry from clients. Used internally on user left'''
		unid = int(unid)
		if unid in self._clients:
			del self._clients[int(unid)]

	def new_client(self, unid, join_time):
		'''Add entry to clients. Used internally on user joined'''
		self._clients[int(unid)] = float(join_time)

class Ban:
	def __init__(self, user: str, ip: str, unid: str, mod: User, time: float):
		self._user = user
		self._ip = ip
		self._unid = unid
		self._mod = mod
		self._time = time

	user = property(lambda self: self._user
		, doc="User that was banned")
	group = property(lambda self: self._user.group
		, doc="Group the Ban belongs to. Sugar for user.group")
	ip = property(lambda self: self._ip
		, doc="IP Address the user was posting from")
	unid = property(lambda self: self._unid
		, doc="Base64 message UID the user was banned from.")
	mod = property(lambda self: self._mod
		, doc="Moderator (a User) the ban was created by")

	def repeal(self):
		'''Sugar for group.unban(self)'''
		self.group.unban(self)

class GroupProtocol(base.ChatangoProtocol):
	'''Protocol for Chatango group commands'''
	def __init__(self, room, manager, loop=None):
		super().__init__(manager, Group(self, room), loop=loop)
		#intermediate message stuff and aux data for commands
		self._messages = {}			#temp message dict for u command lookups
		self._updates = {}			#same as above, but inverted; in case messages get mismatched
		self._history = []			#internal buffer for accumulating historical messages
		self._last_message = 0		#unix epoch of last time message received
		self._history_count = 0		#number of times history has been retrieved
		self._no_more = False		#no more historical messages from the server
		self._last_modlog = 0		#last mod log update; dubiously work

	def connection_made(self, transport):
		'''Begins communication with the server and connects to the room'''
		#if i cared more, i'd put this property-setting in a super method
		super().connection_made(transport)
		self.send_command("bauth", self._storage._name, self._uid, self._manager.username,
			self._manager.password, firstcmd=True)

	#COMMAND PARSING-----------------------------------------------------------
	async def _recv_ok(self, args):
		'''ACK that login succeeded'''
		if args[2] == 'C':
			if self._manager.username:
				self.send_command("blogin", self._manager.username)
			else:
				aid = self._storage._aid
				if aid is not None:
					ncolor = generate.reverse_aid(aid, args[1])
				else:
					ncolor = generate.anon_ncolor()
				self._storage._n_color = ncolor
				self._storage._aid = generate.aid(ncolor, args[1])
		else:
			self._storage._aid = None
		#shouldn't be necessary, but if the room assigns us a new id
		self._uid = int(args[1])
		self._storage._owner = args[0]
		self._storage._mods = set(User.init_mod(self._storage
			, mod.split(',')) for mod in args[6].split(';')) \
			if args[6] else set()

	async def _recv_denied(self, _):
		'''NACK that no such server exists'''
		self._call_event("on_denied")
		await self.disconnect()

	async def _recv_badalias(self, _):
		'''NACK to blogin. Has corresponding ACK, but does nothing'''
		self._call_event("on_bad_alias")

	async def _recv_inited(self, _):
		'''Room inited, after recent messages have sent'''
		self.send_command("gparticipants")		#open up feed for members joining/leaving
		self.send_command("getpremium", '1')	#try to turn on premium features
		self.send_command("getbannedwords")		#what it says on the tin
		self.send_command("getratelimit")		#get posts allowed per n seconds
		self._storage._ready.set()
		self._call_event("on_connect")
		self._call_event("on_history_done", self._history.copy()) #clone history
		self._history.clear()

	async def _recv_gparticipants(self, args):
		'''Command that contains information of current room members'''
		self._storage._users.clear()
		#gparticipants splits people by ;
		people = ':'.join(args[1:]).split(';')
		#room is empty except anons
		if people[0]:
			for person in people:
				self._storage._users.add(
					User.init_g_participant(self._storage, person.split(':')))
		self._call_event("on_participants")

	async def _recv_participant(self, args):
		'''New member joined or left'''
		participant, joined = User.init_participant(self._storage, args)
		if joined:
			if isinstance(participant, User):
				self._storage._users.add(participant)
			self._call_event("on_member_join", participant)
		else:
			self._call_event("on_member_leave", participant)

	async def _recv_n(self, args):
		'''Number of users, in base 16'''
		self._storage._usercount = int(args[0], 16)
		self._call_event("on_usercount")

	async def _recv_bw(self, args):
		'''Banned words'''
		parts = parse.unquote(args[0])
		words = parse.unquote(args[1])
		self._storage._banned_parts = parts.split(',')
		self._storage._banned_words = words.split(',')

	async def _recv_b(self, args):
		'''Message received'''
		post = Post.normal(self._storage, args)
		if post.time > self._last_message:
			self._last_message = post.time
		if post.pnum in self._updates:
			post.unid = self._updates[post.pnum]
			self._call_event("on_message", post)
			del self._updates[post.pnum]
		else: #wait for push by update message
			self._messages[post.pnum] = post

	async def _recv_u(self, args):
		'''Message updated'''
		post = self._messages.get(args[0])
		if post is not None:
			del self._messages[args[0]]
			post.unid = args[1]
			self._call_event("on_message", post)
		else:
			self._updates[args[0]] = args[1]

	async def _recv_i(self, args):
		'''Historical message'''
		post = Post.history(self._storage, args)
		if post.time > self._last_message:
			self._last_message = post.time
		self._history.append(post)

	async def _recv_annc(self, args):
		'''Automatic message'''
		post = Post.announcement(self._storage, args)
		self._call_event("on_announce", post)

	async def _recv_getannc(self, args):
		'''Retrieve announcement'''
		post = Post.announcement(self._storage, args, mod=True)
		self._call_event("on_got_announcement", post)

	async def _recv_gotmore(self, _):
		'''Received all historical messages'''
		self._call_event("on_history_done", self._history.copy())
		self._history.clear()
		self._history_count += 1

	async def _recv_nomore(self, _):
		self._no_more = True
		self._call_event("on_no_more")

	async def _recv_ratelimitset(self, args):
		self._storage._ratelimit = int(args[1])
		self._call_event("on_ratelimit", self._storage.ratelimit)

	async def _recv_show_fw(self, _):
		'''Flood warning'''
		self._call_event("on_flood_warning")

	async def _recv_show_tb(self, args):
		'''Flood ban'''
		self._call_event("on_flood_ban", int(args[0]))

	async def _recv_tb(self, args):
		'''Flood ban reminder'''
		self._call_event("on_flood_ban_repeat", int(args[0]))

	async def _recv_groupflagsupdate(self, args):
		'''Flags updated'''
		self._storage._settings = GroupFlags(int(args[0]))
		self._call_event("on_settings_update")

	async def _recv_updgroupinfo(self, args):
		'''Group info (title and MOTD) updated'''
		self._call_event("on_groupinfo_update", args[0], args[1])

	async def _recv_modactions(self, args):
		ret = [ModLog(self._storage, action)
			for action in ':'.join(args).split(';')]
		#TODO not sure this is actually how it works
		self._last_modlog = ret[-1].unid
		self._storage._modlog.extend(ret)
		self._call_event("on_modlog_update", ret)

	async def _recv_blocklist(self, args):
		'''Received list of banned users'''
		self._storage._banlist.clear()
		sections = ':'.join(args).split(';')
		for section in sections:
			params = section.split(':')
			if len(params) != 5: #sanity check
				continue
			#find the moderator responsible in the list of mods
			source = params[4].lower()
			for mod in self._storage.mods:
				if str(mod).lower() == source:
					source = mod
					break
			ban = Ban(params[2], params[1], params[0], source, float(params[3]))
			self._storage._banlist.append(ban)
		self._call_event("on_banlist_update")

	async def _recv_blocked(self, args):
		'''User banned'''
		source = args[3].lower()
		for mod in self._storage.mods:
			if str(mod).lower() == source:
				source = mod
				break
		ban = Ban(args[2], args[1], args[0], source, float(args[4]))
		self._call_event("on_ban", ban)
		self._storage.request_banlist()

	async def _recv_unblocked(self, args):
		'''User unbanned'''
		for ban in self._storage._bans:
			if args[0] == ban.unid:
				self._call_event("on_unban", ban)
				break
		self._storage.request_banlist()

	async def _recv_mods(self, args):
		'''Moderators changed'''
		new = set(User.init_mod(self._storage, mod.split(','))
			for mod in args)
		old = self._storage._mods
		for mod in new - old: #modded
			self._storage._mods.add(mod)
			self._call_event("on_mod_add", mod)
		for mod in old - new: #demodded
			self._storage._mods.remove(mod)
			self._call_event("on_mod_remove", mod)
		self._call_event("on_mod_change")

	async def _recv_delete(self, args):
		'''Message deleted'''
		self._call_event("on_message_delete", args[0])

	async def _recv_deleteall(self, args):
		'''Message delete (multiple)'''
		for msgid in args:
			self._call_event("on_message_delete", msgid)
	#--------------------------------------------------------------------------

class Group(base.Connection):
	'''Class for high-level group communication and storing group information'''
	_MAX_LENGTH = 2000
	_TOO_BIG_MESSAGE = BIGMESSAGE_MULTIPLE
	def __init__(self, protocol, room):
		super().__init__(protocol)
		#user information
		self._name = room				#group name
		self._owner = None				#owner of the server
		self._users = set()
		self._usercount = 0
		self._ready = asyncio.Event()
		#mod data
		self._settings = None			#inited to mod flags in recv_ok if mod
		self._mods = set()				#set of Users with mod_flags set
		self._banned_parts = []			#parts of words that are banned
		self._banned_words = []			#entire words that are banned
		self._bans = []					#list of Bans
		self._ratelimit = 0
		self._modlog = []

	#########################################
	#	Properties
	#########################################

	@property
	def username(self):
		'''Display name within the group'''
		if self._aid is not None:
			return "!anon" + str(self._aid)
		ret = self._protocol._manager.username
		if not self._protocol._manager.password:
			ret = '#' + ret
		return ret
	session_id = property(lambda self: self._protocol._uid
		, doc="Session ID. Different from client ID, which uniquely identifies each client/tab")
	name = property(lambda self: self._name
		, doc="Name of the group")
	owner = property(lambda self: self._owner
		, doc="Name of the owner of the group")
	users = property(lambda self: [user for user in self._users if user.clients]
		, doc="List of Users in the group")
	usercount = property(lambda self: self._usercount
		, doc="User count")
	last_message = property(lambda self: self._protocol._last_message
		, doc="A float containing the time of the last post obtained")
	ready = property(lambda self: self._ready.wait()
		, doc="Awaitable property for when the group is fully connected")
	#mod attributes
	settings = property(lambda self: self._settings
		, doc="GroupFlags currently active in the group or None, if not mod")
	mods = property(lambda self: self._mods.copy()
		, doc="List of Users. Moderator only.")
	bans = property(lambda self: self._bans.copy()
		, doc="List of Bans. Moderator only.")
	banned_words = property(lambda self: (self._banned_words.copy()
		, self._banned_parts.copy())
		, doc="A 2-tuple of lists of partially banned words and "\
			"totally banned words")
	ratelimit = property(lambda self: self._modlog
		, doc="Rate limit. One message allowed per this many seconds")
	modlog = property(lambda self: self._modlog.copy()
		, doc="A list of ModLog objects: the most recent moderator actions")

	def send_post(self, post: str, channel=0, replace_html=True, badge=0):
		'''Send a post to the group'''
		#TODO allow badge sending
		if not post:
			return
		channel = (((channel&2)<<2 | (channel&1))<<8)
		if replace_html:
			#replace HTML equivalents
			post = html.escape(post)
			post = post.replace('\n', "<br/>")
		if len(post) > self._MAX_LENGTH:
			if self._TOO_BIG_MESSAGE == BIGMESSAGE_CUT:
				self.send_post(post[:self._MAX_LENGTH], channel=channel, replace_html=False)
			elif self._TOO_BIG_MESSAGE == BIGMESSAGE_MULTIPLE:
				while post:
					sect = post[:self._MAX_LENGTH]
					post = post[self._MAX_LENGTH:]
					self.send_post(sect, channel, replace_html=False)
			return
		self._protocol.send_command("bm", "meme", str(channel)
			, ("<n{0._n_color}/><f x{0._f_size:02d}{0._f_color}="\
			  "\"{0._f_face}\">{1}").format(self, post))

	def get_more(self, amt=20):
		'''Get more historical messages'''
		if not self._protocol._no_more:
			self._protocol.send_command("get_more", str(amt)
				, str(self._protocol._history_count))

	def has_permission(self, flags):
		'''Get whether the current user has permissions for a mod action'''
		if self.username == self._owner:
			return True
		for mod in self._mods:
			if mod.name == self.username:
				return bool(mod.mod_flags & flags)
		return False

	def set_anon(self, id_number: int):
		'''Set anon ID to 4 digit number `id_number`'''
		if self.owner is not None: #received an ok
			self._n_color = generate.reverse_aid(str(id_number), self._aid)
		else:
			self._aid = str(int(id_number) % 10000).zfill(4)

	###########################################################################
	# Moderation
	###########################################################################

	def add_mod(self, mod: str, admin=False):
		'''Add moderator'''
		if self.has_permission(2):
			self._protocol.send_command("addmod", mod
				, ModFlags.ADMIN if admin else ModFlags.MODERATOR)

	def remove_mod(self, mod: User):
		'''Remove moderator'''
		if self.has_permission(2):
			if mod not in self._mods:
				return
			self._protocol.send_command("removemod", mod.name)

	def disable_content(self, images=False, links=False, video=False):
		'''Disable images, links, or videos'''
		if self.has_permission(8):
			GroupFlags.update(self._protocol
				, (images, 32), (links, 64), (video, 128))

	def ban_words(self, partial=None, total=None):
		'''
		Set partially banned words (e.g. shithole -> *hole)
		and totally banned words (e.g. shithole -> *)
		Arguments can be strings or lists of strings to ban.
		Words cannot contain ','
		'''
		if self.has_permission(8):
			if isinstance(partial, str):
				partial = [partial]
			if isinstance(partial, list):
				self._banned_parts.extend(
					filter(lambda x: ',' not in x, partial))

			if isinstance(total, str):
				total = [total]
			if isinstance(total, list):
				self._banned_words.extend(
					filter(lambda x: ',' not in x, total))

			self._protocol.send_command("setbannedwords"
				, parse.quote(','.join(self._banned_parts))
				, parse.quote(','.join(self._banned_words)))

	def unban_words(self, partial=None, total=None):
		'''
		Remove a banned word from the list of banned words.
		See ban_words documentation
		'''
		if self.has_permission(8):
			if isinstance(partial, str):
				partial = [partial]
			if isinstance(partial, list):
				for part_ban in partial:
					try:
						self._banned_parts.remove(part_ban)
					except ValueError:
						pass

			if isinstance(total, str):
				total = [total]
			if isinstance(total, list):
				for total_ban in partial:
					try:
						self._banned_totals.remove(total_ban)
					except ValueError:
						pass

			self._protocol.send_command("setbannedwords"
				, parse.quote(','.join(self._banned_parts))
				, parse.quote(','.join(self._banned_words)))

	def disable_anons(self, disable=True):
		if self.has_permission(16):
			GroupFlags.update(self._protocol, (disable, 4))

	def set_rate_limit(self, duration=0):
		'''Set rate limt to one post allowed per `duration` seconds'''
		if self.has_permission(16):
			self._protocol.send_command("ratelimitset", duration)

	def clearall(self):
		'''Clear all messages'''
		if self.has_permission(32):
			self._protocol.send_command("clearall")

	def disable_usercount(self, disable=True):
		if self.has_permission(32):
			GroupFlags.update(self._protocol, (disable, 16))

	def disable_channels(self, disable=True):
		if self.has_permission(32):
			GroupFlags.update(self._protocol, (disable, 8192))

	def request_banlist(self):
		'''Request updated banlist'''
		if self.has_permission(192):
			self._protocol.send_command("blocklist", "block", "", "next", "500")

	def delete(self, message: Post):
		'''Delete a message'''
		if self.has_permission(192):
			self._protocol.send_command("delmsg", message.unid)

	def clear_user(self, message: Post):
		'''Delete all of a user's messages.'''
		if self.has_permission(192):
			self._protocol.send_command("delallmsg", message.mod_id, message.ip, "")

	def ban(self, message: Post):
		'''Ban a user from a message.'''
		if self.has_permission(192):
			self._protocol.send_command("block", message.user, message.ip
				, message.unid)

	def unban(self, ban):
		'''Repeal a ban. Ban must be a username or in `_bans`'''
		if not self.has_permission(192):
			return False
		if isinstance(ban, (str, User)):
			for record in self._bans:
				if str(record.user) == str(ban):
					ban = record
					break
		elif not isinstance(ban, Ban):
			raise TypeError("can only unban Ban objects and usernames")
		elif ban not in self._bans:
			return False
		self._protocol.send_command("removeblock", ban.unid, ban.ip
			, str(ban.user))
		return True

	def get_moderation_log(self, entry_count=50):
		'''
		Retrieve `entry_count` more entries from the moderation log.
		Does nothing until `on_moderation_log` is fired.
		'''
		if self.has_permission(256):
			self._protocol.send_command("getmodactions", "prev"
				, self._protocol._last_modlog, entry_count)

	def auto_moderation(self, basic=False, repetitious=False, advanced=False):
		'''
		Set auto moderation settings. Options are basic filtering, repetious
		filtering, and advanced filtering
		'''
		if self.has_permission(512):
			GroupFlags.update(self._protocol
				, (basic, 16384), (repetitious, 32768), (advanced, 2097152))

	def get_announcement(self):
		'''
		Retrieve the current recurring announcement.
		Does nothing until `on_retrieve_announcement` is fired.
		'''
		if self.has_permission(1024):
			self._protocol.send_command("getannouncement")

	def set_announcement(self, message: str, repeat_duration: int
	, f_color=None):
		'''
		Set announcement to repeat `message` every `repeat_duration` seconds.
		If `message` is the empty string, the announcement is disabled.
		If `f_color` is none, the group's `f_color` is used.
		'''
		if self.has_permission(1024):
			if not message:
				self._protocol.send_command("updateannouncement", 0)
				return
			f_color = self._f_color if f_color is None else f_color
			self._protocol.send_command("updateannouncement", 1, repeat_duration
				, "<f x{:02d}=\"\">{}".format(f_color
				, message[:self._MAX_LENGTH]))

	def set_input(self, closed_without_mods=False, broadcast=False):
		'''Set the group to broadcast mode or to be closed without moderators'''
		if self.has_permission(32768):
			GroupFlags.update(self._protocol
				, (closed_without_mods, 65536), (broadcast, 131072), radio=True)

	def reload_users(self):
		self._protocol.send_command("gparticipants","stop")
		self._protocol.send_command("gparticipants")

	def display_badge(self, choose, force=False):
		'''Set badge display settings'''
		if self.has_permission(0):
			GroupFlags.update(self._protocol
				, (choose, 524288), (force, 1048576), radio=True)

class ModLog:
	'''Moderator log entry. Translates mnemonics into human-readable actions'''
	_REASONS = {
		  "enlp": "Changed group flags:\n"
		, "hidi": "Hid staff badges"
		, "chsi": "Allowed mods to choose badges"
		, "shwi": "Forced mods to show badges"
		, "annc": "{} announcement"
		, "prxy": "{} posting from proxies and VPNs"
		, "chrl": "Set rate limit:\n"
		, "cinm": "Set group to close without moderators"
		, "brdc": "Set group to broadcast mode"
		, "anon": "{} anons"
		, "chan": "{} channels"
		, "emod": "Changed {}'s permissions:\n"
		, "aadm": "Made {} an admin"
		, "amod": "Made {} a moderator"
		, "egrp": "Edited group title/MOTD"
		, "cntr": "Counter {}"
		, "chbw": "Updated banned words"
		, "acls": "Room closed because no moderators"
		, "aopn": "Room opened upon moderator login"
	}
	def __init__(self, group: Group, args):
		args = args.split(',')
		self._group = group
		self._unid = args[0]
		self._mnemonic = args[1]
		moderator = args[2] if args[2] != "None" else None
		if moderator is not None:
			for mod in group._mods:
				if mod == moderator:
					moderator = mod
					break
		self._mod = moderator
		self._ip = args[3] if args[3] != "None" else None
		self._target = args[4] if args[3] != "None" else None
		self._time = float(args[5])
		self._args = json.loads(args[7])

	group = property(lambda self: self._group
		, doc="The Group the log entry belongs to")
	unid = property(lambda self: self._unid
		, doc="Log entry id number. Different from Post.unid and Ban.unid")
	ip = property(lambda self: self._unid
		, doc="IP address the action was taken from")
	time = property(lambda self: self._time
		, doc="Time the action was taken")
	args = property(lambda self: self._args
		, doc="Arguments of the action. Probably less useful than `action`")
	@property
	def action(self):
		'''Explanation of moderator action taken'''
		ret = self._REASONS.get(self._mnemonic)
		if ret is None:
			return "(no explanation found)"
		#flags changed
		if self._mnemonic == "enlp":
			enable, disable = GroupFlags(self.args[0]), GroupFlags(self.args[1])
			ret += "Enabled: {}\nDisabled: {}".format(enable.explain()
				, disable.explain())
		#announcement
		elif self._mnemonic == "annc":
			if self._args[1] != '0':
				ret = ret.format("Set")
				ret += " repeating every {} seconds: {}".format(self._args[1]
					, parse.unquote(self._args[2]))
			else:
				ret = ret.format("Disabled")
		#rate limit
		elif self._mnemonic == "chrl":
			if self._args > 0:
				ret += "{} seconds".format(self._args)
			else:
				ret += "Flood-controlled"
		#edited moderator permissions
		elif self._mnemonic == "emod":
			ret = ret.format(str(self._mod))
			enable, disable = \
				ModFlags(self.args[0]), ModFlags(self.args[1])
			ret += "Enabled: {}\nDisabled: {}".format(enable.explain()
				, disable.explain())
		#admin/mod
		elif self._mnemonic in ("aadm", "amod"):
			ret = ret.format(self._target)
		#standard allowed/disallowed
		elif self._mnemonic in ("prxy", "anon", "chan"):
			ret = ret.format("Allowed" if self._args else "Disallowed")
		elif self._mnemonic == "cntr":
			ret = ret.format("enabled" if self._args else "disabled")
		return ret

class GroupFlags(base.Flags):
	'''Group attributes that mods can change'''
	_EXPLAIN = [
		  None							#1
		, None							#2
		, "Anons disabled"				#4
		, None							#8
		, "User counter disabled"		#16
		, "Images disabled"				#32
		, "Links disabled"				#64
		, "Video embeds disabled"		#128
		, None							#256
		, None							#512
		, "Send censored messages to author only"
		, "Slow mode active (implied by rate limit)"
		, None							#4096
		, "Channels disabled"			#8192
		, "Basic nonsense detection"	#16384
		, "Block repetitious messages (limits messages to 850 bytes)"
		, "Broadcast mode active"		#65536
		, "Closed without moderators"	#131072
		, None							#262144
		#badges are hidden by default
		, "Force staff badges visible"	#524288
		#cannot be on at the same time as the previous
		, "Let mods choose badge visibility"
		, "Advanced nonsense detection"	#2097152
		, "Ban proxies and vpns"		#4194304
	]
	_IMPLIES = {
		  32768:	16384
		, 65536:	131072
		, 2097152:	16384
	}

	@classmethod
	def update(cls, protocol: GroupProtocol, *args, radio=False):
		'''
		Update group flags based on implications.
		`args` elements are formatted as 2-tuples of (bool, flag).
		`radio` implies that only one of `args` can be true, and
		clears the others, like a radio button
		'''
		set_flags, clear_flags = 0, 0
		radio_set = None
		for test, flag in args:
			if cls._IMPLIES.get(flag):
				flag |= cls._IMPLIES[flag]
			#only allow one flag, the first occurrence in args
			if radio:
				if test and radio_set is None:
					radio_set = flag
				else:
					clear_flags |= flag
				continue
			#normal clearing/setting
			if not test:
				clear_flags |= flag
				set_flags &= ~flag
				continue
			set_flags |= flag
			clear_flags &= ~flag

		if radio and radio_set is not None:
			set_flags = radio_set
		protocol.send_command("updategroupflags", set_flags, clear_flags)

class ModFlags(base.Flags):
	_EXPLAIN = [
		  None							#1
		, "Add and remove mods"			#2
		, None							#4
		, "Set banned content"			#8
		, "Set chat	restrictions"		#16
		, "Edit group (title, MOTD, delete all)"
		, "Delete messages"				#64
		, "Ban/unban users"				#128
		, "See mod actions"				#256
		, "Set auto-moderation"			#512
		, "Set group announcement"		#1024
		, None							#2048
		, None							#4096
		, "Exempt from sending limits"	#8192
		, "Can see IP addresses"		#16384
		, "Close group input"			#32768
		, "Can post in broadcast mode"	#65536
		, "Displaying mod badge"		#131072
		, "Can display staff badge"		#262144
	]
	_IMPLIES = {
		  2:		82368
		, 32768:	65536 | 16
	}
	MODERATOR = 354300
	ADMIN = 82368
