import asyncio
from . import generate

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

class ChatangoProtocol(asyncio.Protocol):
	'''Virtual class interpreting chatango's protocol'''
	_PING_DELAY = 15
	_LONGEST_PING = 60
	def __init__(self, manager, storage, loop=None):
		self._loop = manager.loop if loop is None else loop
		self._storage = storage
		self._manager = manager
		self._ping_task = None
		#socket stuff
		self._transport = None
		self._last_command = -1
		self._rbuff = b""
		self.connected = False
		#user id
		self._uid = generate.uid()

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
				receive = getattr(self, "_recv_"+args[0])
				self._loop.create_task(receive(args[1:]))
			except AttributeError:
				pass
		self._rbuff = commands[-1]
		self._last_command = self._loop.time()

	def connection_made(self, transport):
		'''Save the transport and set last command time'''
		self.connected = True
		self._transport = transport
		self._last_command = self._loop.time()
		#create a ping
		self._ping_task = self._loop.create_task(self.ping())

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
		self._call_event("on_disconnect")

	async def ping(self):
		'''Send a ping to keep the transport alive'''
		while self._loop.time() - self._last_command < self._LONGEST_PING:
			await asyncio.sleep(self._PING_DELAY)
			self.send_command("")
		self._transport.close()

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
		self._n_color = None
		self._f_size  = 11
		self._f_color = ""
		self._f_face  = 0

	####################################
	# Properties
	####################################

	n_color = property(lambda self: self._n_color
		, doc="Name color formatting. Cannot set while anonymous.")
	f_color = property(lambda self: self._f_color
		, doc="Main post color formatting.")
	f_size = property(lambda self: self._f_size
		, doc="Font size. Limited to integers 9-14")
	@property
	def f_face(self):
		'''Font face. Can be an integer or valid font name.'''
		if isinstance(self._f_face, int):
			return FONT_FACES[self._f_face]
		return self._f_face

	@n_color.setter
	def n_color(self, arg: str):
		if self._aid is None:
			try:
				int(arg, 16)
			except ValueError:
				raise ValueError("n_color must be a valid hex color")
			self._n_color = arg

	@f_color.setter
	def f_color(self, arg: str):
		if arg:
			try:
				int(arg, 16)
			except ValueError:
				raise ValueError("f_color must be a valid hex color")
		self._f_color = arg

	@f_size.setter
	def f_size(self, arg: int):
		self._f_size = min(22, max(9, arg))

	@f_face.setter
	def f_face(self, arg):
		if isinstance(arg, str):
			if not arg.isdigit():
				self._f_face = arg
				return
			arg = int(arg)
		self._f_face = min(len(FONT_FACES), max(0, arg))

class Flags:
	'''Base class that explains bitwise flags and can set/clear them'''
	_EXPLAIN = []
	_IMPLIES = {}

	def __init__(self, value: int):
		self._dict = {}
		for i, flag in enumerate(self._EXPLAIN):
			self._dict[2**i] = flag
		self._value = value

	def set(self, flag):
		'''Set a flag and all those implied by it'''
		if flag in self._IMPLIES:
			self._value |= self._IMPLIES[flag]
		self._value |= flag

	def clear(self, flag):
		'''Clear a flag, unless implied by another'''
		for old_flag, imply in self._IMPLIES.values():
			if (self._value & old_flag) and (flag & imply):
				return
		self._value &= ~flag

	def explain(self):
		ret = []
		for i, desc in enumerate(self._EXPLAIN):
			flag = 2**i
			if not flag & self._value:
				continue
			if desc is not None:
				if self._IMPLIES.get(flag) is not None:
					desc += "(implies %s)" % ", ".join(2**i
						for i in range(len(self._EXPLAIN))
						if (2**i)&self._IMPLIES[flag])
				ret.append(desc)
			else:
				ret.append("(unknown flag)")
		return ret
