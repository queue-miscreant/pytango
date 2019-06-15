#!/usr/bin/env python3
#private.py
'''
Objects representing private messages and such connections in Chatango.
Implements an asyncio-compatible protocol.
'''
#XXX VERY much a work in progress
#TODO	finish implementing
#
#		when receiving "track" commands in private messages, each "track" download
#		is followed like a `getblock` command
#
#		pending checking if `getblock` is "get tracking block" or "get blocklist" (which is null)
#
import re
import html

from urllib import parse, request
from . import base
from .post import Post

def pm_auth(username, password):
	'''Request auth cookie for PMs'''
	data = parse.urlencode({
		  "user_id":		username
		, "password":		password
		, "storecookie":	"on"
		, "checkerrors":	"yes"
	}).encode()

	login = request.urlopen("http://chatango.com/login", data=data)
	for i in login.headers.get_all("Set-Cookie"):
		search = re.search("auth.chatango.com=(.*?);", i)
		if search:
			return search.group(1)
	return ""

class PMProtocol(base.ChatangoProtocol):
	'''Protocol for Chatango private message commands'''
	def __init__(self, manager, authkey, loop=None):
		super().__init__(manager, Privates(self), loop=loop)
		self.auth_key = authkey

	def connection_made(self, transport):
		'''Begins communication with and connects to the PM server'''
		super().connection_made(transport)
		self.send_command("tlogin", self.auth_key, self._uid, firstcmd=True)

	async def _recv_seller_name(self, *args):
		#seller_name returns two arguments: the session id called with tlogin and
		#the username; neither of these are important except as a sanity check
		pass

	async def _recv_OK(self, _):
		self._call_event("on_pm_connect")
		self.send_command("settings")
		self.send_send_cnd("wl")	#friends list
		self._ping_task = self._loop.create_task(self.ping())

	async def _recv_msg(self, args):
		post = Post.private(self._storage, args)
		self._call_event("on_pm", post, False)

	async def _recv_msgoff(self, args):
		post = Post.private(self._storage, args)
		self._call_event("on_pm", post, True)

	async def _recv_wl(self, args):
		'''Received friends list (watch list)'''
		self._storage._watchList = {}
		it = iter(args)
		try:
			for i in it:
				#username
				self._storage._watchList[i] = (
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
		track = self._storage._trackList
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
		self._storage._watchList[args[0]] = (
			  float(args[2])
			, args[1])
		self._call_event("on_watchlist_update")

	async def _recv_wldelete(self, args):
		'''Received deletion from watch list'''
		#0:	username
		#1:	'deleted'
		#2:	0
		if self._storage._watchList.get(args[0]):
			del self._storage._watchList[args[0]]
		self._call_event("on_watchlist_update")

	async def _recv_status(self, args):
		'''Received status update'''
		#0: username
		#1: last time online
		#2:	online/offline/app
		#update watch
		if self._storage._watchList.get(args[0]):
			self._storage._watchList[args[0]] = (
				  float(args[1])
				, args[2])
			self._call_event("on_watchlist_update")
		#update track
		if self._storage._trackList.get(args[0]):
			self._storage._trackList[args[0]] = (
				  float(args[1])
				, args[2])
			self._call_event("on_track")

class Privates(base.Connection):
	'''High-level private message interface'''
	def __init__(self, protocol):
		super().__init__(protocol)

		self._watch = {}	#analogous to a friends list, but not mutual
		self._track = {}	#dict of users to whom `track` has been issued

	def send_post(self, user, post, replace_html=True):
		if replace_html:
			#replace HTML equivalents
			post = html.escape(post)
			post = post.replace('\n', "<br/>")
		self._protocol.send_command("msg", user, "<m>{}</m>".format(post))
