#!/usr/bin/env python3
#post.py
'''
Post formatting classes. Takes data received from chatango and formats HTML
out, makes formatting easily accessible, and provides mod interfaces.
'''
import re
import html
from . import generate, base

POST_TAG_RE = re.compile("(<n([a-fA-F0-9]{1,6})\\/>)?" \
	"(<f x([0-9a-fA-F]{2,8})=\"([0-9a-zA-Z]*)\">)?")
XML_TAG_RE = re.compile("(</?(.*?)/?>)")
THUMBNAIL_FIX_RE = re.compile(r"(https?://ust\.chatango\.com/.+?/)t(_\d+.\w+)")
REPLY_RE = re.compile(r"@(\w+?)\b")

def parse_formatting(raw):
	'''Parse the strange proprietary HTML formatting tags that chatango has'''
	n_color, f_color, f_size, f_face = '', '', 11, base.FONT_FACES[0]
	tag = POST_TAG_RE.search(raw)
	if tag:
		n_color = tag.group(2) or ''
		size_color = tag.group(4)
		if size_color:
			if len(size_color) % 3 == 2:	#color and font size
				f_size = int(size_color[:2])
				f_color = size_color[2:]
			else:							#no font size
				f_color = size_color
		f_face = tag.group(5)
		try:
			f_face = base.FONT_FACES[int(f_face)]
		except (TypeError, IndexError): #f_face is None or invalid index
			f_face = base.FONT_FACES[0]
		except ValueError: #conversion failed, literal font name
			pass
	return n_color, f_color, f_size, f_face

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
	raw = html.unescape(raw)
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
	def __init__(self, time: float, post: str, group: base.Connection
	, **kwargs):
		self.time = time
		self.post = format_raw(post)
		self.group = group
		self.__dict__.update(kwargs)

	def __eq__(self, other):
		if not hasattr(self, "unid"):
			return False
		if isinstance(other, type(self)):
			return self.unid == other.unid
		return self.unid == other

	@classmethod
	def _base(cls, group: base.Connection, raw):
		n_color, f_color, f_size, f_face = \
			parse_formatting(raw[9])

		message = ':'.join(raw[9:])

		user = raw[1]
		uid = int(raw[3]) #user session id
		if not user:
			if raw[2]: #temp name
				user = '#' + raw[2]
			else:
				user = "!anon" + generate.aid(n_color, raw[3])
			#n_color doesn't count for anons, because it changes their number
			n_color = ''
		else:
			for group_user in group._users:
				if uid in group_user.sessions:
					user = user
					break

		mentions = set()
		for mention in REPLY_RE.findall(message):
			for group_user in group._users:
				if group_user == mention:
					mention = group_user
					break
			mentions.add(mention)

		channels_and_badge = int(raw[7])
		#magic that turns no badge into 0, mod badge into 1, and staff badge into 2
		badge = (channels_and_badge >> 5) & 3
		#magic that turns no channel into 0, red into 1, blue into 2, both into 3
		channel = (channels_and_badge >> 8) & 31
		channel = channel&1|((channel&8)>>2)|((channel&16)>>3)

		return cls(float(raw[0]), message, group, user=user, user_id=uid
			, mod_id=raw[4], unid=None, pnum=None, ip=raw[6]
			, mentions=mentions, channel=channel, badge=badge
			, n_color=n_color, f_color=f_color, f_size=f_size, f_face=f_face)

	@classmethod
	def normal(cls, group: base.Connection, raw):
		ret = cls._base(group, raw)
		ret.pnum = raw[5]
		return ret

	@classmethod
	def history(cls, group: base.Connection, raw):
		ret = cls._base(group, raw)
		ret.unid = raw[5]
		return ret

	@classmethod
	def announcement(cls, group: base.Connection, raw, mod=False):
		'''
		Parse annc and getannc.
		For annc, raw is:
			0: enabled
			1: the group name
			2: the message
		For getannc, raw is:
			0: enabled
			1: the group name
			2: junk (literal 5?)
			3: duration in seconds
			4: the message
		'''
		startmsg = 2 if not mod else 4
		n_color, f_color, f_size, f_face = \
			parse_formatting(raw[startmsg])
		ret = cls(0, ':'.join(raw[startmsg:]), group
			, user=group.name, duration=None, enabled=None
			, n_color=n_color, f_color=f_color, f_size=f_size, f_face=f_face)
		if mod:
			ret.enabled = bool(int(raw[0]))
			ret.duration = int(raw[3])

	@classmethod
	def private(cls, group: base.Connection, raw):
		n_color, f_color, f_size, f_face = \
			parse_formatting(raw[5])
		return cls(float(raw[3]), ':'.join(raw[5:]), group
			, user=raw[0]
			, n_color=n_color, f_color=f_color, f_size=f_size, f_face=f_face)

	def delete(self):
		'''Sugar for group.delete(self)'''
		try:
			if self.unid is not None:
				self.group.delete(self)
		except AttributeError:
			pass

	def deleteall(self):
		'''Sugar for group.deleteall(self)'''
		try:
			if self.mod_id:
				self.group.deleteall(self)
		except AttributeError:
			pass

	def ban(self):
		'''Sugar for group.ban(self)'''
		try:
			if self.unid is not None:
				self.group.ban(self)
		except AttributeError:
			pass

	def my_message(self):
		'''Returns whether a message was sent by the group object it's associated with'''
		return str(self.group.session_id).find(str(self.user_id)) == 0
