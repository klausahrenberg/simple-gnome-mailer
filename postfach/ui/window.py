"""Hauptfenster: Hamburger-Drawer | Nachrichtenliste (+Suche) | Lesebereich."""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import badge, APP_ID, APP_NAME, __version__, config, contacts, db, goa, imapsync
from ..i18n import _, ngettext
from ..mailparse import display_subject
from .accounts import AddAccountDialog, SettingsDialog
from .. import colors, composer, logos
from .compose import ComposeWindow
from .navtree import NavNode, NavTree
from .reader import Reader
from .rows import MessageItem, MessageRow

# Demo-Modus (POSTFACH_DEMO=1): kein Konten-/Kontakte-/Netzzugriff – für Screenshots und Entwicklung mit Testdaten
DEMO = bool(os.environ.get("POSTFACH_DEMO"))
PAGE = 300
ROLE_ORDER = ["inbox", "drafts", "sent", "archive", "junk", "trash"]
ROLE_ICON = {"inbox": "mail-inbox-symbolic", "drafts": "document-edit-symbolic", "sent": "mail-send-symbolic",
             "archive": "folder-download-symbolic", "junk": "mail-mark-junk-symbolic", "trash": "user-trash-symbolic"}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        w, h = config.get("window_size")
        super().__init__(application=app, default_width=int(w), default_height=int(h), title=APP_NAME)
        self.view = dict(config.get("last_view") or {"kind": "unified"})
        self.search_text = ""
        self.offset = 0
        self.store = Gio.ListStore.new(MessageItem)
        self._search_id = 0
        self._labels = {}
        self._colors = {}

        self.sync = imapsync.SyncManager(lambda *a: GLib.idle_add(self._sync_done, *a),
                                         lambda *a: GLib.idle_add(self._sync_status, *a),
                                         lambda msgs: GLib.idle_add(self._notify_new, msgs))

        self._goa = None
        if goa.available() and not DEMO:
            try:
                self._goa = goa.Goa.Client.new_sync(None)
                goa.import_accounts(self._goa)
                for sig in ('account-added', 'account-removed', 'account-changed'):
                    self._goa.connect(sig, lambda *_: self._goa_changed())
            except Exception:
                self._goa = None
        self._build()
        self._actions()
        self._reader_enabled(False)
        self._rebuild_drawer()
        self.reload_list()
        if not db.accounts():
            GLib.idle_add(self._first_run)
        if not DEMO:
            self.sync.start()
            GLib.timeout_add(1500, self._check_passwords)
        self._load_contacts()
        logos.on_ready(self._logos_ready)
        self.connect("close-request", self._on_close)

    def _check_passwords(self):
        """IMAP-Konten ohne gespeichertes Passwort (z. B. nach Umstellung der Kennung): Passwort erfragen."""
        from .. import secrets as keyring
        from .accounts import ask_password
        missing = [a for a in db.accounts() if a["kind"] == "imap" and keyring.lookup(a["id"], "password") is None]
        if missing:
            ask_password(self, missing[0], lambda: self._check_passwords())
        return False

    def _load_contacts(self):
        """Kontaktfotos im Hintergrund laden; danach Liste und Lesebereich neu zeichnen."""
        if DEMO or not contacts.available():
            return

        def work():
            if contacts.load():
                GLib.idle_add(self._contacts_ready)

        threading.Thread(target=work, daemon=True, name="contacts").start()

    def _logos_ready(self):
        """Ein Logo wurde geladen: Zeilen (gedrosselt) und Lesebereich neu zeichnen."""
        if not getattr(self, "_logo_pending", False):
            self._logo_pending = True

            def redraw():
                self._logo_pending = False
                logos.forget_memory()
                self._touch_all()
                if self.reader.pk:
                    self.reader.show_message(self.reader.pk)
                return False

            GLib.timeout_add(800, redraw)

    def _contacts_ready(self):
        self._touch_all()
        if self.reader.pk:
            self.reader.show_message(self.reader.pk)
        return False

    def _notify_new(self, msgs):
        """GNOME-Benachrichtigung für neu eingetroffene Nachrichten (nicht, solange das Fenster im Vordergrund ist)."""
        if self.is_active() or not msgs:
            return False
        app = self.get_application()
        if len(msgs) == 1:
            m = msgs[0]
            n = Gio.Notification.new(m["from_name"] or m["from_addr"] or _("New message"))
            n.set_body(display_subject(m["subject"]))
            n.set_default_action_and_target("app.open-message", GLib.Variant("i", m["pk"]))
            app.send_notification(f"msg-{m['pk']}", n)
        else:
            n = Gio.Notification.new(ngettext("{n} new message", "{n} new messages", len(msgs)).format(n=len(msgs)))
            n.set_body("\n".join(f"{m['from_name'] or m['from_addr']}: {display_subject(m['subject'])}" for m in msgs[:4]))
            n.set_default_action("app.show")
            app.send_notification("new-mail", n)
        return False

    def show_message_pk(self, pk):
        """Wählt eine Nachricht (z. B. aus einer Benachrichtigung) aus und zeigt sie an."""
        self.present()
        self.view = {"kind": "unified"}
        self.search.set_text("")
        self._select_drawer_row()
        self.reload_list()
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).pk == pk:
                self._select_pos(i)
                break

    def _on_close(self, *_):
        if not self.is_maximized() and self.get_width() > 1:
            config.set("window_size", [self.get_width(), self.get_height()])
        app = self.get_application()
        if config.get("run_in_background") and app.tray and app.tray.conn:
            app.keep_running()
            self.set_visible(False)      # weiterlaufen; Öffnen über Benachrichtigung oder Statusleiste
            return True
        self.shutdown()
        return False

    def shutdown(self):
        if not getattr(self, "_down", False):
            self._down = True
            config.set("last_view", {k: x for k, x in self.view.items() if k != "title"})
            self.sync.stop()

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.drawer = Adw.OverlaySplitView(collapsed=True, max_sidebar_width=320, min_sidebar_width=260)
        self.toasts.set_child(self.drawer)
        self.drawer.set_sidebar(self._build_drawer())

        self.split = Adw.NavigationSplitView(min_sidebar_width=280, max_sidebar_width=2000,
                                             sidebar_width_fraction=self._clamp_fraction(config.get("list_fraction")))
        self.drawer.set_content(self.split)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        # --- Liste
        lv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.menu_btn = Gtk.ToggleButton(icon_name="postfach-menu-symbolic", tooltip_text=_("Menu (accounts, settings)"))
        self.menu_btn.bind_property("active", self.drawer, "show-sidebar", GObject.BindingFlags.BIDIRECTIONAL)
        hb.pack_start(self.menu_btn)
        self.title = Adw.WindowTitle(title=_("Inbox"))
        hb.set_title_widget(self.title)
        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)
        lv.add_top_bar(hb)

        # Werkzeugzeile über dem Suchfeld: Abrufen, Verfassen | Stern, Ungelesen, Archivieren, Löschen
        tools = Gtk.Box(spacing=2, margin_start=8, margin_end=8, margin_top=2, margin_bottom=2)

        def tool(icon, tip, act):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=f"win.{act}")
            b.add_css_class("flat")
            return b

        tools.append(tool("view-refresh-symbolic", _("Fetch (F5)"), "refresh"))
        tools.append(tool("document-edit-symbolic", _("New message (Ctrl+N)"), "compose"))
        tools.append(Gtk.Box(hexpand=True))
        self.star_btn = tool("non-starred-symbolic", _("Flag"), "star")
        tools.append(self.star_btn)
        tools.append(tool("mail-unread-symbolic", _("Mark as unread"), "unread"))
        tools.append(tool("folder-download-symbolic", _("Archive"), "archive"))
        self.spam_btn = tool("mail-mark-junk-symbolic", _("Mark as spam"), "spam")
        tools.append(self.spam_btn)
        tools.append(tool("user-trash-symbolic", _("Delete (Del)"), "delete"))
        lv.add_top_bar(tools)

        self.search = Gtk.SearchEntry(placeholder_text=_("Search …  from:name  file:pdf"), hexpand=True,
                                 tooltip_text=_("Full-text search. Filters: from:anna · from:\"anna smith\" · to:name · subject:text · file:pdf · is:unread · is:flagged"))
        self.search.set_margin_start(10)
        self.search.set_margin_end(10)
        self.search.set_margin_top(4)
        self.search.set_margin_bottom(8)
        self.search.connect("search-changed", self._on_search)
        lv.add_top_bar(self.search)

        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", lambda f, li: li.set_child(MessageRow()))
        self.factory.connect("bind", lambda f, li: li.get_child().bind(li.get_item()))
        self.factory.connect("unbind", lambda f, li: li.get_child().unbind())
        self.sel = Gtk.MultiSelection(model=self.store)     # Strg/Umschalt-Klick, Ziehen, Strg+A
        self.sel.connect("selection-changed", self._on_selected)
        self.listview = Gtk.ListView(model=self.sel, factory=self.factory, single_click_activate=False,
                                   enable_rubberband=True)
        self.listview.add_css_class("navigation-sidebar")
        self.scroll = Gtk.ScrolledWindow(child=self.listview, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.connect("edge-reached", self._edge)

        self.empty = Adw.StatusPage(icon_name="mail-unread-symbolic", title=_("No messages"))
        self.stack = Gtk.Stack()
        self.stack.add_named(self.scroll, "list")
        self.stack.add_named(self.empty, "empty")
        lv.set_content(self.stack)
        # Ziehbarer Teiler am rechten Rand der Liste
        self.handle = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.FILL, width_request=8)
        self.handle.add_css_class("pane-handle")
        self.handle.set_cursor_from_name("col-resize")
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda g, x, y: setattr(self, "_drag_w", self.list_page.get_width()))
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", lambda *_: config.set("list_fraction", self.split.get_sidebar_width_fraction()))
        self.handle.add_controller(drag)
        ov = Gtk.Overlay(child=lv)
        ov.add_overlay(self.handle)
        self.list_page = ov
        self.split.set_sidebar(Adw.NavigationPage(child=ov, title=_("Messages")))
        self.split.connect("notify::collapsed", lambda *_: self.handle.set_visible(not self.split.get_collapsed()))
        css = Gtk.CssProvider()
        css.load_from_string(colors.css() + ".pane-handle { background: transparent; } .pane-handle:hover { background: alpha(@accent_bg_color, .45); }"
                         " .unread-dot { background: @accent_bg_color; border-radius: 50%; min-width: 9px; min-height: 9px; }"
                         " .unread-time { color: @accent_color; font-weight: bold; }")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # --- Lesebereich
        rv = Adw.ToolbarView()
        rh = Adw.HeaderBar()
        rh.set_show_title(False)
        self.reader_btns = []

        def btn(icon, tip, act):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=f"win.{act}")
            self.reader_btns.append(b)
            return b

        rv.add_top_bar(rh)
        self.reader = Reader(self)
        self.rstack = Gtk.Stack()
        self.rstack.add_named(Adw.StatusPage(icon_name="mail-read-symbolic", title=_("No message selected")), "empty")
        self.rstack.add_named(self.reader, "msg")
        self.multi_page = Adw.StatusPage(icon_name="edit-select-all-symbolic", title=_("Several messages selected"))
        mbox = Adw.WrapBox(child_spacing=12, line_spacing=12, align=0.5, justify=Adw.JustifyMode.NONE)
        self.multi_spam = None
        for label, act, css in ((_("Delete"), "delete", "destructive-action"), (_("Archive"), "archive", None), (_("Mark as spam"), "spam", None),
                                (_("Mark as read"), "mark-read", None), (_("Mark as unread"), "unread", None)):
            b = Gtk.Button(label=label, action_name=f"win.{act}")
            if act == "spam":
                self.multi_spam = b
            b.add_css_class("pill")
            if css:
                b.add_css_class(css)
            mbox.append(b)
        self.multi_page.set_child(mbox)
        self.rstack.add_named(self.multi_page, "multi")
        rv.set_content(self.rstack)
        self.split.set_content(Adw.NavigationPage(child=rv, title=_("Reading")))

    @staticmethod
    def _clamp_fraction(f):
        try:
            return min(0.7, max(0.2, float(f)))
        except (TypeError, ValueError):
            return 0.36

    def set_list_fraction(self, f, save=True):
        f = self._clamp_fraction(f)
        self.split.set_sidebar_width_fraction(f)
        if save:
            config.set("list_fraction", f)

    def _on_drag(self, gesture, dx, dy):
        total = self.split.get_width()
        if total > 0:
            self.set_list_fraction((self._drag_w + dx) / total, save=False)

    def _build_drawer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        hb.set_title_widget(Adw.WindowTitle(title=_("Mailboxes")))
        box.append(hb)
        self.nav = NavTree(self._on_nav_view)
        sc = Gtk.ScrolledWindow(child=self.nav.view, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(sc)
        box.append(Gtk.Separator())
        bottom = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        bottom.add_css_class("navigation-sidebar")
        for icon, text, act in (("list-add-symbolic", _("Add account"), "add-account"),
                                ("emblem-system-symbolic", _("Settings"), "settings"),
                                ("help-about-symbolic", _("About {app}").format(app=APP_NAME), "about"),
                                ("application-exit-symbolic", _("Quit"), "quit")):
            row = Gtk.ListBoxRow()
            row.act = act
            b = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            b.append(Gtk.Image.new_from_icon_name(icon))
            b.append(Gtk.Label(label=text))
            row.set_child(b)
            bottom.append(row)

        def activated(lb, r):
            self.drawer.set_show_sidebar(False)
            if r.act == "quit":
                self.get_application().quit_app()
            else:
                self.activate_action(f"win.{r.act}", None)

        bottom.connect("row-activated", activated)
        box.append(bottom)
        return box

    def _color_of(self, acc_id):
        return self._colors.get(acc_id, 0)

    def _rebuild_drawer(self):
        counts = db.unread_counts()
        accs = db.accounts()
        self._labels = {a["id"]: a["email"] for a in accs}
        self._colors = {a["id"]: colors.index_for(a, i) for i, a in enumerate(accs)}
        inbox_total = 0
        acc_nodes = []
        for a in accs:
            col = self._colors[a["id"]]
            folders = list(db.folders(a["id"]))
            roles = sorted((f for f in folders if f["role"] != "other"),
                           key=lambda f: ROLE_ORDER.index(f["role"]) if f["role"] in ROLE_ORDER else 99)
            kids, acc_unread = [], 0
            for f in roles:
                n = counts.get((a["id"], f["name"]), 0)
                if f["role"] == "inbox":
                    inbox_total += n
                    acc_unread = n
                if f["role"] in ("trash", "junk", "drafts", "sent"):
                    n = 0
                title = imapsync.folder_title(f["role"], f["name"], f["delim"], f["title"])
                kids.append(NavNode(f"f:{a['id']}:{f['name']}", title, ROLE_ICON.get(f["role"], "folder-symbolic"),
                                    {"kind": "folder", "account_id": a["id"], "folder": f["name"],
                                     "title": f"{title} · {a['email']}"}, n))
            kids += self._folder_tree(a, [f for f in folders if f["role"] == "other"], counts)
            acc_nodes.append(NavNode(f"acc:{a['id']}", a["email"], None, None, acc_unread, color=col, children=kids, bold=True))
        top = [NavNode("unified", _("Unified inbox"), "mail-inbox-symbolic", {"kind": "unified"}, inbox_total),
               NavNode("unread", _("Unread"), "mail-unread-symbolic", {"kind": "unread"}),
               NavNode("flagged", _("Flagged"), "starred-symbolic", {"kind": "flagged"}),
               NavNode("sent", _("Sent"), "mail-send-symbolic", {"kind": "sent"})]
        self.nav.set_nodes(top + acc_nodes)
        badge.set_count(inbox_total)
        if self.get_application().tray:
            self.get_application().tray.set_unread(inbox_total)
        self._select_drawer_row()

    def _folder_tree(self, acc, folders, counts):
        """Weitere Ordner als verschachtelte Knoten (Pfad am Trennzeichen geteilt)."""
        root = {}
        for f in folders:
            if f["title"] or not f["delim"]:
                parts = [f["title"] or imapsync.utf7_decode(f["name"])]
            else:
                parts = [imapsync.utf7_decode(p) for p in f["name"].split(f["delim"])]
            d = root
            for i, part in enumerate(parts):
                ent = d.setdefault(part, {"kids": {}, "folder": None})
                if i == len(parts) - 1:
                    ent["folder"] = f
                d = ent["kids"]

        def build(d, path):
            out = []
            for part in sorted(d, key=str.lower):
                ent, f = d[part], d[part]["folder"]
                key = f"d:{acc['id']}:{path}/{part}"
                kids = build(ent["kids"], f"{path}/{part}")
                if f:
                    view = {"kind": "folder", "account_id": acc["id"], "folder": f["name"],
                            "title": f"{part} · {acc['email']}"}
                    out.append(NavNode(f"f:{acc['id']}:{f['name']}", part, "folder-symbolic", view,
                                       counts.get((acc["id"], f["name"]), 0), children=kids))
                else:
                    out.append(NavNode(key, part, "folder-symbolic", None, 0, children=kids))
            return out

        return build(root, "")

    def _select_drawer_row(self):
        v = self.nav.find_view(self.view)
        if v:
            self.view["title"] = v.get("title")
        self.nav.select_view(self.view)
        self.title.set_title(self._view_title())

    def _view_title(self):
        k = self.view.get("kind")
        return {"unified": _("Unified inbox"), "unread": _("Unread"), "flagged": _("Flagged"), "sent": _("Sent")}.get(k) \
            or self.view.get("title") or _("Inbox")

    def _on_nav_view(self, v):
        self.view = dict(v)
        config.set("last_view", {k: x for k, x in v.items() if k != "title"})
        self.title.set_title(self._view_title())
        self.drawer.set_show_sidebar(False)
        self.search.set_text("")
        self.split.set_show_content(False)
        self.reload_list()
        if v.get("kind") == "folder":
            self.sync.request_folder(v["account_id"], v["folder"])

    def refresh_colors(self):
        """Nach Änderung einer Kontofarbe: Baum und Liste neu zeichnen."""
        self._rebuild_drawer()
        self.reload_list()

    # -------------------------------------------------------------- Aktionen
    def _actions(self):
        def add(name, cb, accel=None):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
            if accel:
                self.get_application().set_accels_for_action(f"win.{name}", [accel])

        add("compose", lambda: self._compose())
        add("refresh", lambda: self.sync.request())
        add("search", lambda: self.search.grab_focus(), "<Control>f")
        add("reply", self._reply, "<Control>r")
        add("reply-all", lambda: self._reply(True), "<Control><Shift>r")
        add("forward", self._forward, "<Control>l")
        add("delete", self._delete, "Delete")
        add("archive", self._archive)
        add("spam", self._spam)
        add("star", self._star)
        add("unread", self._mark_unread)
        add("mark-read", self._mark_read)
        add("add-account", lambda: AddAccountDialog(self.accounts_changed).present(self))
        add("settings", lambda: SettingsDialog(self).present(self))
        add("about", self._about)
        self.get_application().set_accels_for_action("win.compose", ["<Control>n"])
        self.get_application().set_accels_for_action("win.refresh", ["F5"])

    def _about(self):
        Adw.AboutDialog(application_name=APP_NAME, version=__version__, developer_name="Klaus Ahrenberg",
                        comments=_("A lean mail client for GNOME"), application_icon=APP_ID,
                        license_type=Gtk.License.GPL_3_0).present(self)

    def toast_restart(self, text):
        """Hinweis mit Knopf „Neu starten“ (z. B. nach Sprachwechsel)."""
        t = Adw.Toast(title=text, button_label=_("Restart"), timeout=0)
        t.connect("button-clicked", lambda *_a: self.get_application().restart())
        self.toasts.add_toast(t)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _first_run(self):
        AddAccountDialog(self.accounts_changed).present(self)

    def _goa_changed(self):
        before = {(a["id"], a["imap_host"], a["email"]) for a in db.accounts()}
        goa.import_accounts(self._goa)
        if before != {(a["id"], a["imap_host"], a["email"]) for a in db.accounts()}:
            self.accounts_changed(None)
            self.sync.refresh_idle()

    def accounts_changed(self, new_id):
        self._rebuild_drawer()
        self.reload_list()
        self.sync.request(new_id)
        self.sync.refresh_idle()

    # --------------------------------------------------------------- Liste
    def reload_list(self):
        self.offset = 0
        self.store.remove_all()
        self._load_more()

    def _new_item(self, r, multi):
        it = MessageItem(r, self._labels.get(r["account_id"], "") if multi else "", self._colors.get(r["account_id"]))
        # in Gesendet-Ansichten zählt der Empfänger, nicht der (eigene) Absender
        it.show_to = self.view.get("kind") == "sent" or (
            self.view.get("kind") == "folder" and db.folder_by_role(r["account_id"], "sent") == r["folder"])
        it.on_star = self._star_item
        return it

    def _load_more(self):
        rows = db.query(self.view, self.search_text, PAGE, self.offset)
        multi = self.view.get("kind") != "folder" and len(self._labels) > 1
        self.store.splice(self.store.get_n_items(), 0,
                          [self._new_item(r, multi) for r in rows])
        self.offset += len(rows)
        self._more = len(rows) == PAGE
        self._update_empty()

    def _edge(self, sc, pos):
        if pos == Gtk.PositionType.BOTTOM and getattr(self, "_more", False):
            self._load_more()

    def _update_empty(self):
        if self.store.get_n_items():
            self.stack.set_visible_child_name("list")
            return
        self.stack.set_visible_child_name("empty")
        if not db.accounts():
            self.empty.set_icon_name("avatar-default-symbolic")
            self.empty.set_title(_("No account yet"))
            self.empty.set_description(_("Add an account via GNOME Online Accounts or IMAP."))
            b = Gtk.Button(label=_("Add account"), halign=Gtk.Align.CENTER, action_name="win.add-account")
            b.add_css_class("suggested-action")
            b.add_css_class("pill")
            self.empty.set_child(b)
        else:
            self.empty.set_child(None)
            if self.search_text:
                self.empty.set_icon_name("system-search-symbolic")
                self.empty.set_title(_("No results"))
                self.empty.set_description(_("Nothing found for “{query}” in {view}.").format(query=self.search_text, view=self._view_title()))
            else:
                self.empty.set_icon_name("mail-unread-symbolic")
                self.empty.set_title(_("No messages"))
                self.empty.set_description("")

    def _on_search(self, entry):
        self.search_text = entry.get_text().strip()
        self._search_id += 1
        sid = self._search_id
        GLib.timeout_add(200, lambda: (self.reload_list() if sid == self._search_id else None) and False)

    # --------------------------------------------------------- Auswahl/Lesen
    def _selected_positions(self):
        bits = self.sel.get_selection()
        return [bits.get_nth(i) for i in range(bits.get_size())]

    def _selected_items(self):
        return [self.store.get_item(i) for i in self._selected_positions()]

    def _item(self):
        """Das ausgewählte Element – nur bei genau einer Auswahl."""
        pos = self._selected_positions()
        return self.store.get_item(pos[0]) if len(pos) == 1 else None

    def _select_pos(self, i):
        self.sel.unselect_all()
        self.sel.select_item(i, True)

    def _reader_enabled(self, on, multi=False):
        for name in ("reply", "reply-all", "forward"):
            self.lookup_action(name).set_enabled(on and not multi)
        for name in ("delete", "archive", "spam", "unread", "star"):
            self.lookup_action(name).set_enabled(on)

    def _in_junk(self, it):
        return it.d["folder"] == db.folder_by_role(it.d["account_id"], "junk")

    def _update_spam_button(self, items):
        """Liegen alle gewählten Mails im Spam-Ordner, heißt die Aktion „Kein Spam“ (zurück in den Posteingang)."""
        not_spam = bool(items) and all(self._in_junk(i) for i in items)
        self.spam_btn.set_icon_name("mail-mark-notjunk-symbolic" if not_spam else "mail-mark-junk-symbolic")
        self.spam_btn.set_tooltip_text(_("Not spam – back to the inbox") if not_spam else _("Mark as spam"))
        if self.multi_spam:
            self.multi_spam.set_label(_("Not spam") if not_spam else _("Mark as spam"))

    def _on_selected(self, *_):
        if getattr(self, "_suppress", False):
            return
        items = self._selected_items()
        self._update_spam_button(items)
        if not items:
            self.rstack.set_visible_child_name("empty")
            self._reader_enabled(False)
            return
        if len(items) > 1:
            self.multi_page.set_title(ngettext("{n} message selected", "{n} messages selected", len(items)).format(n=len(items)))
            self.rstack.set_visible_child_name("multi")
            self._reader_enabled(True, multi=True)
            self.split.set_show_content(True)
            return
        it = items[0]
        pk = it.pk
        self.reader.show_message(pk)
        self.split.set_show_content(True)  # nur relevant im schmalen Layout
        if self.reader.row and self.reader.row["partial"]:
            self._load_full(pk)
        self.rstack.set_visible_child_name("msg")
        self._reader_enabled(True)
        self._sync_star_icon(it)
        if not it.d["seen"]:
            self._set_flag([it], "seen", True)

    def load_full(self, pk, then=None):
        """Lädt eine nur teilweise synchronisierte Nachricht vollständig und ruft danach then() auf."""
        self._load_full(pk, then)

    def _load_full(self, pk, then=None):
        def work():
            try:
                imapsync.fetch_full(pk)
                GLib.idle_add(done)
            except Exception as e:
                msg = _("The message could not be loaded completely: {error}").format(error=e)
                GLib.idle_add(lambda: self.toast(msg) and False)

        def done():
            if self.reader.pk == pk:
                self.reader.show_message(pk)
            if then:
                then()
            return False

        threading.Thread(target=work, daemon=True).start()

    def _sync_star_icon(self, it):
        self.star_btn.set_icon_name("starred-symbolic" if it.d["flagged"] else "non-starred-symbolic")

    def _refresh_item(self, it):
        it.emit("changed")          # die gebundene Zeile zeichnet sich selbst neu (Auswahl bleibt unberührt)

    def _touch_all(self):
        for i in range(self.store.get_n_items()):
            self.store.get_item(i).emit("changed")

    def _star_item(self, it):
        """Klick auf den Stern in der Zeile: Markierung umschalten (der Stern ist nur bei markierten Mails sichtbar)."""
        self._set_flag([it], "flagged", not it.d["flagged"])
        if self._item() is it:
            self._sync_star_icon(it)

    def _bg(self, fn, err_msg):
        if DEMO:                       # Demo-Modus: keine Serveraktionen
            return

        def work():
            try:
                fn()
            except Exception as e:
                msg = f"{err_msg}: {e}"
                GLib.idle_add(lambda: self.toast(msg) and False)
        threading.Thread(target=work, daemon=True).start()

    def _set_flag(self, items, which, value):
        """Setzt „gelesen“ bzw. „markiert“ für alle Elemente (lokal sofort, auf dem Server gesammelt)."""
        key = "seen" if which == "seen" else "flagged"
        flag = "\\Seen" if which == "seen" else "\\Flagged"
        pks = []
        self._suppress = True
        try:
            for it in items:
                it.d[key] = int(value)
                db.set_flags(it.pk, **{key: value})
                pks.append(it.pk)
                self._refresh_item(it)
        finally:
            self._suppress = False
        self._bg(lambda: imapsync.remote_set_flag(pks, flag, value), _("Server action failed"))
        self._rebuild_drawer_soon()

    def _rebuild_drawer_soon(self):
        GLib.idle_add(lambda: self._rebuild_drawer() and False)

    def _star(self):
        items = self._selected_items()
        if items:
            value = not all(i.d["flagged"] for i in items)
            self._set_flag(items, "flagged", value)
            self._sync_star_icon(items[0])

    def _mark_unread(self):
        if items := self._selected_items():
            self._set_flag(items, "seen", False)

    def _mark_read(self):
        if items := self._selected_items():
            self._set_flag(items, "seen", True)

    def _remove_selected(self, remote_fn, msg_fn):
        items = self._selected_items()
        if not items:
            return
        pks = [it.pk for it in items]
        positions = self._selected_positions()
        first = positions[0]

        def work():
            try:
                remote_fn(pks)
                for pk in pks:
                    db.delete_message(pk)
            except Exception as e:
                err = _("Failed: {error}").format(error=e)
                GLib.idle_add(lambda: (self.toast(err), self.reload_list()) and False)

        # optimistisch aus der Liste nehmen; DB-Zeilen bleiben, bis der Server bestätigt hat
        self._suppress = True
        try:
            for pos in sorted(positions, reverse=True):
                self.store.remove(pos)
        finally:
            self._suppress = False
        n = self.store.get_n_items()
        if n:
            self._select_pos(min(first, n - 1))
        else:
            self.sel.unselect_all()
            self._on_selected()
            self._update_empty()
        threading.Thread(target=work, daemon=True).start()
        self.toast(msg_fn(len(pks)))

    def _delete(self):
        items = self._selected_items()
        if not items:
            return
        n = len(items)
        permanent = [it for it in items if db.folder_by_role(it.d["account_id"], "trash") in (None, it.d["folder"])]
        if not permanent:
            body = ngettext("The message will be moved to the trash.", "The messages will be moved to the trash.", n)
        elif len(permanent) == n:
            body = ngettext("The message will be deleted permanently and cannot be restored.",
                            "The messages will be deleted permanently and cannot be restored.", n)
        else:
            body = _("The messages will be deleted – {k} of them permanently, the rest will go to the trash.").format(k=len(permanent))
        d = Adw.AlertDialog(heading=ngettext("Delete message?", "Delete {n} messages?", n).format(n=n), body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("delete", _("Delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "delete" and self._remove_selected(imapsync.remote_delete, lambda n: ngettext("Message deleted", "{n} messages deleted", n).format(n=n)))
        d.present(self)

    def _archive(self):
        items = [it for it in self._selected_items() if db.folder_by_role(it.d["account_id"], "archive")]
        if not items:
            self.toast(_("No archive folder in this account"))
            return
        self.sel.unselect_all()
        for it in items:
            for i in range(self.store.get_n_items()):
                if self.store.get_item(i) is it:
                    self.sel.select_item(i, False)
        self._remove_selected(imapsync.remote_move_archive, lambda n: ngettext("Message archived", "{n} messages archived", n).format(n=n))

    def _select_items(self, items):
        self.sel.unselect_all()
        for it in items:
            for i in range(self.store.get_n_items()):
                if self.store.get_item(i) is it:
                    self.sel.select_item(i, False)

    def _spam(self):
        items = self._selected_items()
        if items and all(self._in_junk(i) for i in items):          # „Kein Spam“
            if not all(db.folder_by_role(i.d["account_id"], "inbox") for i in items):
                self.toast(_("No inbox in this account"))
                return
            self._remove_selected(imapsync.remote_not_spam, lambda n: ngettext("Message moved back to the inbox", "{n} messages moved back to the inbox", n).format(n=n))
            return
        items = [it for it in items if db.folder_by_role(it.d["account_id"], "junk") and not self._in_junk(it)]
        if not items:
            self.toast(_("No spam folder in this account, or the message is already in spam"))
            return
        self._select_items(items)
        self._remove_selected(imapsync.remote_mark_spam, lambda n: ngettext("Message moved to the spam folder", "{n} messages moved to the spam folder", n).format(n=n))

    # -------------------------------------------------------------- Verfassen
    def _compose(self, **kw):
        acc = self.view.get("account_id")
        if "body_text" in kw:
            kw["body"] = kw.pop("body_text")
        kw = composer.apply_format_policy(kw)
        ComposeWindow(self, account_id=kw.pop("account_id", acc), **kw).present()

    def compose_to(self, to, subject="", body=""):
        self._compose(to=to, subject=subject, body=body)

    def _with_full(self, pk, cb):
        """Antworten/Weiterleiten brauchen die vollständige Nachricht (bei großen Mails erst nachladen)."""
        m = db.message(pk)
        if m and m["partial"]:
            self.toast(_("Loading the complete message …"))
            self.load_full(pk, cb)
        else:
            cb()

    def _reply(self, reply_all=False):
        if not (it := self._item()):
            return

        def go():
            self._compose(**composer.build_reply(db.message(it.pk), db.raw_message(it.pk) or b"", reply_all))

        self._with_full(it.pk, go)

    def _forward(self):
        if not (it := self._item()):
            return

        def go():
            self._compose(**composer.build_forward(db.message(it.pk), db.raw_message(it.pk) or b""))

        self._with_full(it.pk, go)

    # ------------------------------------------------------------------- Sync
    def _sync_status(self, state, text):
        busy = state == "sync"
        self.spinner.set_spinning(busy)
        self.spinner.set_tooltip_text(text if busy else None)

    def _sync_done(self, acc_id, n, err):
        if err:
            self.toast(err[:200])
        if (n or not err) and not getattr(self, "_refresh_pending", False):
            self._refresh_pending = True
            GLib.timeout_add(1200, self._refresh_after_sync)
        return False

    def _refresh_after_sync(self):
        self._refresh_pending = False
        if contacts.stale():
            self._load_contacts()
        self._rebuild_drawer()
        if not self.search_text:
            keep = {it.pk for it in self._selected_items()}
            self._suppress = True      # Auswahl/Lesebereich bleiben beim Neuaufbau unverändert
            try:
                self.reload_list()
                for i in range(self.store.get_n_items()):
                    if self.store.get_item(i).pk in keep:
                        self.sel.select_item(i, False)
            finally:
                self._suppress = False
        return False
