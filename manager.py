#!/usr/bin/env python3
#manager.py
'''
The manager class and associated helper functions. Provides a single unified
object to manage all connections and interpret events.
'''
#TODO: possibly provide a better interface
import asyncio
from socket import gaierror
from urllib import request
from os.path import basename
from functools import partial
from . import base, group, private, generate

def _connection_lost_handler(loop, context):
	failed_protocol = context.get("protocol")
	if isinstance(failed_protocol, base.ChatangoProtocol):
		loop.create_task(failed_protocol.disconnect(True))
	else:
		loop.default_exception_handler(context)

def get_anon(name):
	if name.find("anon") == 0 and len(name) == 8 and name[4:].isdigit():
		return int(name[4:])
	return None

class Manager:
	'''
	Creates and manages connections to Chatango.
	Also propagates events from joined groups
	'''
	def __init__(self, username: str, password: str, pm=False, loop=None):
		self.loop = asyncio.get_event_loop() if loop is None else loop
		self._groups = []
		self.privates = None
		if pm:
			self.loop.create_task(self.join_pm())
		self._aid = get_anon(username)
		if self._aid is not None:
			self.username = ""
			self.password = ""
		else:
			self.username = username
			self.password = password

		self.loop.set_exception_handler(_connection_lost_handler)

	def __del__(self):
		if self.loop.is_closed():
			return
		for i in self._groups:
			#disconnect (and cancel all ping tasks)
			self.loop.run_until_complete(i._protocol.disconnect())
		if self.privates is not None:
			self.loop.run_until_complete(self.privates._protocol.disconnect())

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

	async def join_group(self, group_name: str, port=443):
		'''(Coro) Join group `group_name`'''
		group_name = group_name.lower()
		server = generate.server_num(group_name)
		if server is None:
			raise ValueError("malformed room token " + repr(group_name))

		#already joined group
		if group_name != self.username and group_name not in self._groups:
			try:
				ret = group.GroupProtocol(group_name, self)
				if self._aid is not None:
					ret._storage.set_anon(self._aid)
				await self.loop.create_connection(lambda: ret,
					"s{}.chatango.com".format(server), port)
				self._groups.append(ret._storage)
				return ret._storage
			except gaierror as exc:
				raise ConnectionError("could not connect to group server") \
				from exc
		elif self.username and group_name == self.username:
			return await self.join_pm()
		else:
			raise ValueError("attempted to join group multiple times")

	async def leave_group(self, group_name):
		'''(Coro) Leave group `group_name`'''
		if isinstance(group_name, group.Group):
			group_name = group_name.name
		for index, gro in enumerate(self._groups):
			if gro.name == group_name:
				await gro._protocol.disconnect()
				self._groups.pop(index)

	async def join_pm(self, port=5222):
		'''(Coro) Log into private messages and return Connection'''
		if self.privates is None:
			try:
				authkey = private.pm_auth(self.username, self.password)
				ret = private.PMProtocol(self, authkey)
				await self.loop.create_connection(lambda: ret,
					"c1.chatango.com", port)
				self.privates = ret._storage
			except gaierror as exc:
				raise ConnectionError("could not connect to PM server") from exc
		return self.pm

	async def leave_pm(self):
		'''(Coro) If logged into private messages, log out'''
		if self.privates is not None:
			await self.privates._protocol.disconnect()
			self.privates = None

	async def leave_all(self):
		'''(Coro) Disconnect from all groups and PMs'''
		for gro in self._groups:
			await gro._protocol.disconnect()
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
			request.urlopen(_Multipart('http://chatango.com/updateprofile',
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

class _Multipart(request.Request):
	'''Simplified version of requests.post for multipart/form-data'''
	#code adapted from http://code.activestate.com/recipes/146306/
	_MULTI_BOUNDARY = '---------------iM-in-Ur-pr07oc01'
	_DISPOSITION = "Content-Disposition: form-data; name=\"{}\""

	def __init__(self, url, data, headers=None):
		multiform = []
		headers = headers if headers is not None else {}
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
					multiform.append((self._DISPOSITION + \
					"; filename=\"{}\"").format(i, basename(j[1].name)))
					multiform.append("Content-Type: {}".format(j[0]))
				except AttributeError as exc:
					raise ValueError("expected file-like object") from exc
			else:
				#no mime type supplied
				multiform.append(self._DISPOSITION.format(i))
			multiform.append("")
			multiform.append(data)
		multiform.append("--" + self._MULTI_BOUNDARY + "--")
		#encode multiform
		request_body = (b"\r\n").join([i if isinstance(i, bytes) \
			else i.encode() for i in multiform])

		headers.update({
			  "content-length":	str(len(request_body))
			, "content-type":	"multipart/form-data; boundary={}".format(
				self._MULTI_BOUNDARY)
		})
		super().__init__(url, data=request_body, headers=headers)
