#!/usr/bin/python

import time
import sys
import argparse
import re
import getpass
import urwid
import imaplib
import email, email.header
import html2text

#import smtplib

logf = open("pymail.log", 'w')
def log(msg):
	print >> logf, str(msg)
	logf.flush()

msg_id_h = 'Message-ID'
msg_id1_h = 'Message-Id'
msg_repl_h = 'In-Reply-To'
msg_refs_h = 'References'

def h2t(s, cs):
	return html2text.html2text(s.decode(cs))

def decode_header(h):
	sl = []
	for s, enc in email.header.decode_header(h):
		sl.append(s)
	return " ".join(sl)

class att_w(urwid.Text):
	def __init__(self, t):
		self.__super.__init__(t)

	def selectable(self):
		return True

	def keypress(self, sz, k):
		return k

class message_view_w(urwid.ListBox):
	def __init__(self, msg):
		self.msg = msg
		msg.load()
		h = []
		t = []
		a = []

		hs = [time.strftime("%Y-%m-%d %H:%M", msg.time)]
		hk = ('Date', 'From', 'To', 'Cc', 'Subject', 'User-Agent',
			msg_id_h, msg_id1_h, msg_repl_h, msg_refs_h)
		for k in hk:
			if k in msg.email.keys():
				hs.append("%s: %s" % (k, decode_header(msg.email[k])))
		h.append(urwid.AttrWrap(urwid.Text("\n".join(hs)), "msg_hdr"))

		for p in msg.email.walk():
			ct = p.get_content_type()
			cs = p.get_content_charset()
			if ct == "text/plain" and len(t) == 0:
				t.append(urwid.Text(p.get_payload(decode=True)))
			elif ct == "text/html" and len(t) == 0:
				t.append(urwid.Text(h2t(p.get_payload(decode=True), cs)))
			else:
				a.append(urwid.AttrWrap(att_w("<%s>" % ct), 'msg_att'))
		self.__super.__init__(urwid.SimpleListWalker(h + a + t))

	def selectable(self):
		return True

	def keypress(self, sz, k):
		if k in "qQ":
			self.msg.pymail.view.set_body(self.msg.pymail.listbox)
			return None
		self.__super.keypress(sz, k)

class message_w(urwid.TreeWidget):
	def __init__(self, node):
		if not node.refs:
			self.unexpanded_icon = urwid.AttrMap(
				urwid.TreeWidget.expanded_icon,
				'no_expand_msg')
			self.expanded_icon = urwid.AttrMap(
				urwid.TreeWidget.expanded_icon,
				'no_expand_msg')
		else:
			self.unexpanded_icon = urwid.AttrMap(
				urwid.TreeWidget.unexpanded_icon,
				'expand_msg')
			self.expanded_icon = urwid.AttrMap(
				urwid.TreeWidget.expanded_icon,
				'expand_msg')

		self.__super.__init__(node)
		self.expanded = True
		self.update_expanded_icon()
		self._w = urwid.AttrWrap(self._w, None)
		self._w.attr = 'body'
		self._w.focus_attr = 'focus'

	def get_display_text(self):
		node = self.get_node()
		if not node.upstream:
			mark = [("top_msg", "!"), " "]
		else:
			mark = [""]
		if node.empty:
			return mark + [("missing_msg", node.msg_id)]

		t = time.strftime("%Y-%m-%d %H:%M ", node.time)
		s = [("timestamp", t),
		     ("from", decode_header(node.email['From']) + " "),
		     decode_header(node.email['Subject'])]
		return mark + s

	def keypress(self, sz, k):
		n = self.get_node()
		if k == "enter" and not n.empty:
			n.pymail.view.set_body(message_view_w(n))
			return None
		return self.__super.keypress(sz, k)

class message(urwid.ParentNode):
	def __init__(self, parent, key, hdrs, t, empty=False):
		self.empty = empty
		self.imap = parent.imap
		self.pymail = parent.pymail
		self.mbx = parent.get_key()
		self.msg_key = key
		self.email = hdrs
		self.full = False
		self.time = t
		self.replies = {}
		self.refs = 0
		self.top_id = None
		self.parent_id = None
		self.upstream = None
		self.tsort = 0
		self.ref_ids = None
		if empty:
			self.msg_id = key
			return
		self.tsort = time.mktime(t)
		if msg_id_h in hdrs.keys():
			self.msg_id = hdrs[msg_id_h]
		elif msg_id1_h in hdrs.keys():
			self.msg_id = hdrs[msg_id1_h]
		else:
			self.msg_id = None
		if msg_repl_h in hdrs.keys():
			s = hdrs[msg_repl_h]
			r = re.search("(<[^>]+>)", s)
			if r:
				self.parent_id = r.group(1)
		if msg_refs_h in hdrs.keys():
			self.ref_ids = []
			l = hdrs[msg_refs_h].split()
			for s in l:
				r = re.search("(<[^>]+>)", s)
				if r:
					self.ref_ids.append(r.group(1))
		if not self.parent_id and self.ref_ids:
			self.parent_id = self.ref_ids[-1]
		if not self.ref_ids and self.parent_id:
			self.ref_ids = [self.parent_id]
		if self.parent_id and self.parent_id != self.ref_ids[-1]:
			log("WARNING: message %s: parent: %s references: %s" %
				(str(self.msg_id), str(self.parent_id), str(self.ref_ids)))


	def super_init(self, p):
		self.update_sort_time()
		urwid.ParentNode.__init__(self, "", depth = p.get_depth() + 1,
			key = self.msg_key,
			parent = p)
		for r in self.replies.values():
			r.super_init(self)

	def add_reply(self, r):
#		if self.msg_id == None or self.msg_id != r.parent_id:
#			raise NameError, "Message linking error"
		self.replies[r.msg_key] = r
		r.upstream = self
		self.refs += 1

	def update_sort_time(self):
		for r in self.replies.values():
			r.update_sort_time()
			if r.tsort > self.tsort:
				self.tsort = r.tsort

	def load(self):
		if self.full or self.empty:
			return
		t = self.imap.get_msg_full(self.get_key(), self.mbx)
		self.email = email.message_from_string(t)
		self.full = True

	def load_widget(self):
		return message_w(self)

	def load_child_keys(self):
		k = self.replies.keys()
		ret = sorted(k, lambda i,j,m=self.replies:
					int(m[i].tsort - m[j].tsort))
		return ret

	def load_child_node(self, k):
		return self.replies[k]

class mailbox_w(urwid.TreeWidget):
	unexpanded_icon = urwid.AttrMap(urwid.TreeWidget.unexpanded_icon,
		'expand_mbx')
	expanded_icon = urwid.AttrMap(urwid.TreeWidget.expanded_icon,
		'expand_mbx')

	def __init__(self, node):
		self.__super.__init__(node)
		self.expanded = False if node.get_depth() else True
		self.update_expanded_icon()
		self._w = urwid.AttrWrap(self._w, None)
		self._w.attr = 'mailbox'
		self._w.focus_attr = 'focus'

	def get_display_text(self):
		node = self.get_node()
		if node.get_depth() == 0:
			return "/"
		else:
			return node.get_key() + "/"

class mailbox(urwid.ParentNode):
	def __init__(self, key, parent):
		urwid.ParentNode.__init__(self, "", depth = 1,
			key = key, parent = parent)
		self.imap = parent.imap
		self.pymail = parent.pymail

	def load_widget(self):
		return mailbox_w(self)

	def empty_msg(self, msg_id):
		return message(self, msg_id, None, 0, empty=True)

	def load_child_keys(self):
		r = self.imap.get_headers(self.get_key())
		self.msgs = {i : message(self, i, email.message_from_string(r[i][0]), r[i][1])
			for i in r.keys()}

		# create dict using msg_id as key
		msg_by_id = {}
		for m in self.msgs.values():
			if m.msg_id:
				mid = m.msg_id
			else:
				mid = m.msg_key
			msg_by_id[mid] = m

		# add missing messages nodes
		for m in self.msgs.values():
			if m.ref_ids:
				prev = None
				for r in m.ref_ids:
					if not r in msg_by_id.keys():
						p = self.empty_msg(r)
						p.parent_id = prev
						msg_by_id[r] = p
					else:
						p = msg_by_id[r]
					prev = p.msg_id

		# add missing messages back to self.msgs
		for m in msg_by_id.values():
			if not m.msg_key in self.msgs.keys():
				self.msgs[m.msg_key] = m

		# link messages
		for m in self.msgs.values():
			if m.parent_id:
				msg_by_id[m.parent_id].add_reply(m)

		# find unreferenced nodes
		top_keys = [i for i in self.msgs.keys() if not self.msgs[i].upstream]

		# call parent class constructor
		for i in top_keys:
			self.msgs[i].super_init(self)

		return sorted(top_keys,
			      cmp=lambda i, j, m=self.msgs: 
					int(m[j].tsort - m[i].tsort))

	def load_child_node(self, key):
		return self.msgs[key]

class imap_root(urwid.ParentNode):
	def __init__(self, pymail):
		urwid.ParentNode.__init__(self, "", depth=0)
		self.imap = pymail.imap
		self.pymail = pymail

	def load_widget(self):
		return mailbox_w(self)

	def load_child_keys(self):
		return self.imap.get_mailboxes()

	def load_child_node(self, key):
		return mailbox(key, self)

class pymail:
	palette = [
		('body', 'light gray', 'black'),
		('mailbox', 'white', 'black', ('bold')),
		('flagged', 'black', 'dark green', ('bold','underline')),
		('focus', 'light gray', 'dark blue', 'standout'),
		('flagged focus', 'yellow', 'dark cyan',
			('bold','standout','underline')),
		('head', 'yellow', 'dark blue', 'standout'),
		('foot', 'light gray', 'dark blue'),
		('key', 'light cyan', 'black','underline'),
		('title', 'white', 'black', 'bold'),
		('expand_mbx', 'light gray', 'dark blue', 'bold'),
		('expand_msg', 'white', 'dark magenta', 'bold'),
		('no_expand_msg', 'dark cyan', 'dark cyan', 'bold'),
		('flag', 'dark gray', 'light gray'),
		('error', 'dark red', 'light gray'),
		('msg_hdr', 'light cyan', 'black'),
		('msg_att', 'yellow', 'black'),
		('timestamp', 'light cyan', 'black'),
		('from', 'yellow', 'black'),
		('missing_msg', 'light red', 'black'),
		('top_msg', 'white', 'dark red'),
	]

	def __init__(self, imap):
		self.imap = imap
		self.header = urwid.Text("PyMail: %s@%s:%d" %
					 (imap.user, imap.host, imap.port))
		self.listbox = urwid.TreeListBox(urwid.TreeWalker(imap_root(self)))
		self.listbox.offset_rows = 1
		self.footer = urwid.Text("")
		self.view = urwid.Frame(
			urwid.AttrWrap(self.listbox, 'body'),
			header=urwid.AttrWrap(self.header, 'head'),
			footer=urwid.AttrWrap(self.footer, 'foot'))

	def main(self):
		self.loop = urwid.MainLoop(self.view, self.palette,
				unhandled_input=self.unhandled_input)
		self.loop.run()

	def unhandled_input(self, k):
		if k in ('q','Q'):
			raise urwid.ExitMainLoop()

class imap_server:
	def __init__(self, user, password, imap_host, imap_port):
		self.user = user
		self.password = password
		self.host = imap_host
		self.port = imap_port
		self.server = imaplib.IMAP4_SSL(imap_host, imap_port)
		self.server.login(user, password)

	def select(self, m):
		log("select mailbox '%s'" % m)
		self.server.select(m)

	def close(self):
		self.server.close()

	def parse_list_reply(self, s):
		r = re.match('\((.*)\)\s+"(.)"\s+"?([^"]+)"?', s)
		if r:
			return r.group(1), r.group(2), r.group(3)
		raise NameError, "reply string:\n" + s

	def get_mailboxes(self):
		mbxs = []
		ret, r = self.server.list()
		if ret != "OK":
			raise NameError, "Failed to get a list of mailboxes"
		for s in r:
			f, d, m = self.parse_list_reply(s)
			mbxs.append(m)
		return mbxs

	def get_msg_ids(self):
		ret, r = self.server.search(None, "ALL")
		if ret != "OK":
			raise NameError, "Failed to fetch message list"
		ids = map(int, r[0].split())
		if len(ids) == 0:
			return []
		if len(ids) == 1:
			return str(ids[0])
		q = []
		i = ids[0]
		j = i
		for n in ids[1:]:
			j += 1
			if j != n:
				j -= 1
				if j == i:
					q.append("%d" % i)
				else:
					q.append("%d:%d" % (i, j))
				i = n
				j = n
			if n == ids[-1]:
				if i == n:
					q.append("%d" % n)
				else:
					q.append("%d:%d" % (i, n))
		log("messages count: %d" % len(ids))
		log("messages ids: %s" % (",".join(q)))
		return q

	def get_headers(self, m):
		log("fetch headers from '%s'" % m)
		self.select(m)
		q = self.get_msg_ids()
		if len(q) == 0:
			return []
		hdrs = {}
		ret, r = self.server.fetch(",".join(q), "(BODY.PEEK[HEADER])")
		if ret != "OK":
			raise NameError, "Failed to fetch headers" % i
		for p in r:
			if type(p) is tuple:
				i = p[0][:p[0].find(" ")]
				h = p[1]
				hdrs[i] = [h,]
		ret, r = self.server.fetch(",".join(q), "(INTERNALDATE)")
		if ret != "OK":
			raise NameError, "Failed to fetch dates" % i
		self.server.close()
		for p in r:
			i = p[:p.find(" ")]
			d = imaplib.Internaldate2tuple(p)
			hdrs[i].append(d)
		return hdrs

	def get_msg_full(self, i, m):
		self.select(m)
		log("fetch message %s" % str(i))
		ret, r = self.server.fetch(i, "(RFC822)")
		self.server.close()
		if ret != "OK":
			raise NameError, "Failed to fetch message `%s`" % i
		return "".join([pt[1] for pt in r if type(pt) is tuple])

	def logout(self):
		self.server.logout()

def user_host_port(s):
	u, h, p = "", "", 0

	u_hp = s.split("@")
	if len(u_hp) == 1:
		hp = u_hp[0]
	elif len(u_hp) == 2:
		u = u_hp[0]
		hp = u_hp[1]
	else:
		raise NameError, "Malformed user@host:port string: `%s`" % s

	h_p = hp.split(":")
	if len(h_p) == 1:
		h = h_p[0]
	elif len(h_p) == 2:
		h = h_p[0]
		p = int(h_p[1])
	else:
		raise NameError, "Malformed user@host:port string: `%s`" % s

	return u, h, p

def main():
	p = argparse.ArgumentParser(description="PyMail MUA")
	p.add_argument('-i', dest="imap", metavar='IMAP_HOST',
			type=str, help='IMAP host name', required=True)
	p.add_argument('-s', dest="smtp", metavar='SMTP_HOST',
			type=str, help='SMTP host name', default="")
	args = p.parse_args()

	imap_port = 993
	user, imap_host, p = user_host_port(args.imap)
	if p:
		imap_port = p
	if len(user) == 0:
		print "Login: ",
		user = sys.stdin.readline().strip()

	password = getpass.getpass()
	imap = imap_server(user, password, imap_host, imap_port)

	try:
		pymail(imap).main()
	finally:
		imap.logout()
		logf.close()

if __name__ == "__main__":
	main()
