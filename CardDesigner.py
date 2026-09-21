"""Arcanum Card Designer — design cards, publish them to the database.

Run:  python CardDesigner.py          (from the project root)

A dev tool, never shipped to players. Designs any of the six card types with
keyword picking (values for Thorns X etc.), hero types, rarity, stats, art
upload (auto-fitted to the card frame), a live preview, and two outputs:

* Publish  — writes the card to the Supabase `cards` table and uploads the
             art to the `card-art` storage bucket. Requires the SERVICE ROLE
             key (paste it in the field or set ARCANUM_SERVICE_KEY). The
             service key is a SECRET: it never goes in the client or GitHub.
* Save Local — appends to data/designed_cards.json and copies art into
             assets/cards/ for offline testing before publishing.

Hero cards render into the official card template (assets/templates/
hero.png) with live preview. Drag the art inside the window to reposition;
mouse-wheel over it to zoom. Drag-and-drop an image file onto the preview
(needs `pip install tkinterdnd2`; otherwise use Choose Art).

Requires: pip install pillow          (rendering)
Optional: pip install tkinterdnd2     (drag-and-drop)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error as _urlerr
from urllib import request as _urlreq

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from arcanum.core.constants import SUPABASE_ANON_KEY, SUPABASE_URL  # noqa: E402
from arcanum.game.cardspec import (CardSpec, KeywordRef, make_card_id)  # noqa: E402
from arcanum.game.keywords import (HERO_TYPES, KEYWORDS_BY_ID, CardType,  # noqa: E402
                                   Rarity, keywords_for)
try:
    from card_render import (ART_CORE, DEFAULT_FONT_SIZES,  # noqa: E402
                             TEMPLATE_SIZE, renderer_for)
    RENDER_OK = True
except Exception:  # noqa: BLE001 - designer still runs without pillow
    RENDER_OK = False
    DEFAULT_FONT_SIZES = {"name": 48, "type": 32, "cost": 92, "rules": 34,
                          "flavor": 28, "stats": 66}

ART_BOX = (512, 384)          # card frame art window (4:3)
LOCAL_FILE = PROJECT_DIR / "data" / "designed_cards.json"
LOCAL_ART = PROJECT_DIR / "assets" / "cards"


def work_dir() -> Path:
    """Writable scratch space: the project's data folder, else the OS temp
    directory (covers launches from read-only working directories)."""
    candidate = PROJECT_DIR / "data"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return candidate
    except OSError:
        import tempfile
        return Path(tempfile.gettempdir()) / "arcanum_designer"


# ---------------------------------------------------------------------------
# Publishing (pure logic — GUI-free and testable)
# ---------------------------------------------------------------------------
def fit_art(source_path: str, out_path: Path) -> bool:
    """Center-crop + scale the chosen image into the card art box (PNG)."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed (pip install pillow); copying art as-is.")
        out_path.write_bytes(Path(source_path).read_bytes())
        return False
    img = Image.open(source_path).convert("RGB")
    target_w, target_h = ART_BOX
    scale = max(target_w / img.width, target_h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    img.save(out_path, "PNG", optimize=True)
    return True


def save_local(spec: CardSpec, art_path: Path | None) -> str:
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if LOCAL_FILE.exists():
        rows = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    rows = [r for r in rows if r.get("id") != spec.id]
    rows.append({"id": spec.id, "name": spec.name, "data": spec.to_dict(),
                 "collectible": spec.collectible})
    LOCAL_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if art_path is not None and art_path.exists():
        LOCAL_ART.mkdir(parents=True, exist_ok=True)
        (LOCAL_ART / f"{spec.id}.png").write_bytes(art_path.read_bytes())
    return f"Saved locally: {spec.id} -> {LOCAL_FILE}"


def publish(spec: CardSpec, art_path: Path | None,
            service_key: str) -> tuple[bool, str]:
    """Upsert the card row; upload art to the card-art bucket."""
    if not SUPABASE_URL:
        return False, "SUPABASE_URL is not configured in constants.py."
    if not service_key:
        return False, ("Service role key required (paste it, or set "
                       "ARCANUM_SERVICE_KEY). Find it in Supabase -> "
                       "Project Settings -> API keys.")
    base = SUPABASE_URL.rstrip("/")

    # 1) upsert the card row
    if art_path is not None and art_path.exists():
        # fresh filename each publish so player clients never show stale art
        import time as _time
        spec.image = f"{spec.id}-{int(_time.time()) % 100000:05d}.png"
    row = {"id": spec.id, "name": spec.name, "data": spec.to_dict(),
           "collectible": spec.collectible}
    req = _urlreq.Request(
        base + "/rest/v1/cards?on_conflict=id",
        data=json.dumps(row).encode(),
        headers={"apikey": service_key,
                 "Authorization": f"Bearer {service_key}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"},
        method="POST")
    try:
        with _urlreq.urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 201, 204):
                return False, f"Card upsert failed (HTTP {resp.status})."
    except _urlerr.HTTPError as exc:
        detail = exc.read()[:200].decode(errors="replace")
        if exc.code == 404:
            return False, ("The 'cards' table doesn't exist yet — run the "
                           "setup SQL first.")
        if exc.code in (401, 403):
            return False, "That key was rejected — use the service_role key."
        return False, f"Card upsert failed (HTTP {exc.code}): {detail}"
    except (_urlerr.URLError, TimeoutError, OSError) as exc:
        return False, f"Network problem: {exc}"

    # 2) upload art (optional)
    if art_path is not None and art_path.exists():
        object_name = spec.image
        req = _urlreq.Request(
            base + f"/storage/v1/object/card-art/{object_name}",
            data=art_path.read_bytes(),
            headers={"Authorization": f"Bearer {service_key}",
                     "apikey": service_key,
                     "Content-Type": "image/png",
                     "x-upsert": "true"},
            method="POST")
        try:
            with _urlreq.urlopen(req, timeout=30) as resp:
                if resp.status not in (200, 201):
                    return False, f"Art upload failed (HTTP {resp.status})."
        except _urlerr.HTTPError as exc:
            body = exc.read()[:300].decode(errors="replace")
            if exc.code in (400, 404) and "not found" in body.lower():
                return False, ("Card saved, but the 'card-art' storage bucket "
                               "doesn't exist (or is named differently). "
                               "Supabase -> Storage -> New bucket -> name it "
                               "exactly card-art (lowercase, hyphen) and tick "
                               "Public bucket. Then publish again — republish "
                               "is safe.")
            return False, (f"Card saved, but art upload failed "
                           f"({exc.code}): {body}")
        except (_urlerr.URLError, TimeoutError, OSError) as exc:
            return False, f"Card saved, but art upload failed: {exc}"
    return True, f"Published '{spec.name}' ({spec.id}) to the card database."


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
# ------------------------------------------------------------------ helpers
# Field visibility per card type: the form only shows what the type uses.
TYPE_FIELDS = {
    CardType.HERO:     {"attack": True,  "health": True,  "durability": False,
                        "hero_types": True},
    CardType.CHAMPION: {"attack": False, "health": True,  "durability": False,
                        "hero_types": False},
    CardType.MINION:   {"attack": True,  "health": True,  "durability": False,
                        "hero_types": False},
    CardType.SPELL:    {"attack": False, "health": False, "durability": False,
                        "hero_types": False},
    CardType.RELIC:    {"attack": False, "health": False, "durability": False,
                        "hero_types": False},
    CardType.BARRIER:  {"attack": False, "health": False, "durability": True,
                        "hero_types": False},
}

# sensible starting X for value keywords, so "Add" never lands a zero
KW_DEFAULT_VALUES = {"thorns": 1, "charged": 1, "astral": 3, "reanimate": 2,
                     "pack": 1, "countdown": 3, "ritual": 2, "reflect": 1,
                     "fortify": 1, "regenerate": 1}


def resolve_image_name(current_id: str, loaded_id: str,
                       existing_image: str, has_new_art: bool) -> str:
    """Editing an existing card WITHOUT replacing its art must keep the
    old (cache-busted) image name — otherwise every text edit would point
    clients at a file that doesn't exist."""
    if not has_new_art and existing_image and current_id == loaded_id:
        return existing_image
    return f"{current_id}.png"


def fetch_cards(service_key: str):
    """(True, rows) with id/name/collectible/data, or (False, message)."""
    if not SUPABASE_URL:
        return False, "SUPABASE_URL is not configured."
    if not service_key:
        return False, "Paste the service key first."
    url = (SUPABASE_URL.rstrip("/") +
           "/rest/v1/cards?select=id,name,collectible,data&order=name.asc")
    req = _urlreq.Request(url, headers={
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}"})
    try:
        with _urlreq.urlopen(req, timeout=20) as resp:
            rows = json.loads(resp.read() or b"[]")
            return True, rows
    except _urlerr.HTTPError as exc:
        body = exc.read()[:200].decode(errors="replace")
        if exc.code in (401, 403):
            return False, "Key rejected — use the service_role key."
        return False, f"Fetch failed (HTTP {exc.code}): {body}"
    except (_urlerr.URLError, TimeoutError, OSError) as exc:
        return False, f"Network problem: {exc}"


def spec_from_row(row: dict):
    """(CardSpec, image_name) from a DB row; tolerant of older rows."""
    data = dict(row.get("data") or {})
    data.setdefault("id", row.get("id", ""))
    data.setdefault("name", row.get("name", "Unnamed"))
    data.setdefault("card_type", "hero")
    data.setdefault("rarity", "common")
    spec = CardSpec.from_dict(data)
    spec.collectible = bool(row.get("collectible",
                                    data.get("collectible", True)))
    return spec, str(data.get("image", "") or "")


DRAFT_PATH_NAME = "draft.json"


def save_draft(payload: dict) -> None:
    try:
        (work_dir() / DRAFT_PATH_NAME).write_text(
            json.dumps(payload, indent=1))
    except OSError:
        pass


def load_draft():
    try:
        path = work_dir() / DRAFT_PATH_NAME
        if path.exists():
            return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return None


def run() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    NAVY, NAVY2, GOLD, TEXT = "#0d1530", "#16224a", "#d4af37", "#e8e6df"

    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        dnd_available = True
    except Exception:  # noqa: BLE001
        root = tk.Tk()
        dnd_available = False
    root.title("Arcanum Card Designer")
    root.configure(bg=NAVY)
    root.geometry("1180x760")

    state = {"art_source": None,      # chosen file path
             "art_img": None,         # PIL image (original)
             "offset": [0.0, 0.0],    # template-space pan
             "zoom": 1.0}

    # ---------------- left: the form ----------------
    form = tk.Frame(root, bg=NAVY)
    form.pack(side="left", fill="both", expand=True, padx=14, pady=12)

    def label(text, row, col=0):
        tk.Label(form, text=text, bg=NAVY, fg=GOLD,
                 font=("Georgia", 10, "bold")).grid(
            row=row, column=col, sticky="w", pady=(8, 0))

    def entry(row, col=1, width=34):
        widget = tk.Entry(form, width=width, bg=NAVY2, fg=TEXT,
                          insertbackground=TEXT, relief="flat")
        widget.grid(row=row, column=col, sticky="we", pady=(8, 0), padx=6)
        return widget

    label("Name", 0)
    name_entry = entry(0)
    label("Card ID", 1)
    id_entry = entry(1)
    tk.Button(form, text="auto", command=lambda: (
        id_entry.delete(0, "end"),
        id_entry.insert(0, make_card_id(name_entry.get() or "card"))),
        bg=NAVY2, fg=TEXT, relief="flat").grid(row=1, column=2)

    label("Type", 2)
    type_var = tk.StringVar(value="hero")
    type_box = ttk.Combobox(form, textvariable=type_var, state="readonly",
                            values=[t.value for t in CardType], width=14)
    type_box.grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))

    label("Rarity", 3)
    rarity_var = tk.StringVar(value="common")
    ttk.Combobox(form, textvariable=rarity_var, state="readonly",
                 values=[r.value for r in Rarity], width=14).grid(
        row=3, column=1, sticky="w", padx=6, pady=(8, 0))

    stats_label = tk.Label(form, text="Stats", bg=NAVY, fg=GOLD,
                           font=("Georgia", 10, "bold"))
    stats_label.grid(row=4, column=0, sticky="w", pady=(8, 0))
    stat_frame = tk.Frame(form, bg=NAVY)
    stat_frame.grid(row=4, column=1, sticky="w", padx=6, pady=(8, 0))
    stat_entries = {}
    stat_cells = {}
    for stat in ("cost", "attack", "health", "durability"):
        cell = tk.Frame(stat_frame, bg=NAVY)
        cell.pack(side="left", padx=3)
        tk.Label(cell, text=stat.capitalize(), bg=NAVY, fg=TEXT,
                 font=("Georgia", 8)).pack()
        e = tk.Entry(cell, width=5, bg=NAVY2, fg=TEXT, justify="center",
                     insertbackground=TEXT, relief="flat")
        e.insert(0, "0")
        e.pack()
        stat_entries[stat] = e
        stat_cells[stat] = cell

    ht_label = tk.Label(form, text="Hero types (up to 2)", bg=NAVY, fg=GOLD,
                        font=("Georgia", 10, "bold"))
    ht_label.grid(row=5, column=0, sticky="w", pady=(8, 0))
    ht_frame = tk.Frame(form, bg=NAVY)
    ht_frame.grid(row=5, column=1, sticky="w", padx=6, pady=(8, 0))
    ht_vars = [tk.StringVar(value=""), tk.StringVar(value="")]
    for var in ht_vars:
        ttk.Combobox(ht_frame, textvariable=var, state="readonly",
                     values=[""] + list(HERO_TYPES), width=16).pack(
            side="left", padx=3)

    def apply_type_gating(*_a):
        """Only show what this card type actually uses — and clear what it
        can't legally carry, so stale numbers never fail validation."""
        try:
            ct = CardType(type_var.get())
        except ValueError:
            return
        rules = TYPE_FIELDS[ct]
        for stat in ("attack", "health", "durability"):
            if rules[stat]:
                stat_cells[stat].pack(side="left", padx=3)
            else:
                stat_cells[stat].pack_forget()
                stat_entries[stat].delete(0, "end")
                stat_entries[stat].insert(0, "0")
        if rules["hero_types"]:
            ht_label.grid()
            ht_frame.grid()
        else:
            ht_label.grid_remove()
            ht_frame.grid_remove()
            for var in ht_vars:
                var.set("")
        # keywords from another category can't survive a type switch
        legal = {k.id for k in keywords_for(ct)}
        kept = [ref for ref in chosen_keywords if ref.id in legal]
        if len(kept) != len(chosen_keywords):
            chosen_keywords[:] = kept
            kw_list.delete(0, "end")
            for ref in kept:
                kw = KEYWORDS_BY_ID[ref.id]
                shown = (kw.name.format(x=ref.value) if kw.has_value
                         else kw.name)
                kw_list.insert("end", shown)
        update_preview()

    label("Keywords", 6)
    kw_frame = tk.Frame(form, bg=NAVY)
    kw_frame.grid(row=6, column=1, columnspan=2, sticky="we", padx=6,
                  pady=(8, 0))
    kw_pick = ttk.Combobox(kw_frame, state="readonly", width=22)
    kw_pick.pack(side="left")
    kw_value = tk.Entry(kw_frame, width=4, bg=NAVY2, fg=TEXT,
                        insertbackground=TEXT, relief="flat")
    kw_value.pack(side="left", padx=4)
    tk.Label(kw_frame, text="X", bg=NAVY, fg=TEXT).pack(side="left")
    kw_list = tk.Listbox(form, height=6, bg=NAVY2, fg=TEXT,
                         selectbackground=GOLD, relief="flat")
    kw_list.grid(row=7, column=1, columnspan=2, sticky="we", padx=6,
                 pady=(6, 0))
    kw_list.bind("<Double-Button-1>", lambda _e: remove_keyword())
    chosen_keywords: list[KeywordRef] = []
    editing = {"loaded_id": "", "image": ""}   # DB-edit bookkeeping

    kw_by_label = {}

    def refresh_kw_options(*_a):
        try:
            ct = CardType(type_var.get())
        except ValueError:
            return
        kw_by_label.clear()
        options = []
        for k in keywords_for(ct):
            tag = f" (X = {k.charge})" if k.has_value and k.charge else \
                (" (X)" if k.has_value else "")
            shown = f"{k.name.replace(' {x}', '')}{tag}"
            kw_by_label[shown] = k.id
            options.append(shown)
        kw_pick["values"] = options
        kw_pick.set(options[0] if options else "")
        sync_kw_value_box()

    def sync_kw_value_box(*_a):
        kw_id = kw_by_label.get(kw_pick.get(), "")
        kw = KEYWORDS_BY_ID.get(kw_id)
        kw_value.delete(0, "end")
        if kw is not None and kw.has_value:
            kw_value.config(state="normal")
            kw_value.insert(0, str(KW_DEFAULT_VALUES.get(kw_id, 1)))
        else:
            kw_value.config(state="disabled")

    def on_type_change(*_a):
        refresh_kw_options()
        apply_type_gating()

    type_box.bind("<<ComboboxSelected>>", on_type_change)
    kw_pick.bind("<<ComboboxSelected>>", sync_kw_value_box)
    refresh_kw_options()

    def add_keyword():
        kw_id = kw_by_label.get(kw_pick.get(), "")
        if not kw_id:
            return
        if any(ref.id == kw_id for ref in chosen_keywords):
            status.config(text=f"{KEYWORDS_BY_ID[kw_id].name} is already on "
                               "this card.", fg="#e5c98a")
            return
        kw = KEYWORDS_BY_ID[kw_id]
        value = None
        if kw.has_value:
            try:
                value = int(kw_value.get())
                if value < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Keyword", f"{kw.name.replace(' {x}', '')} needs a "
                               "positive number for X.")
                return
        chosen_keywords.append(KeywordRef(kw_id, value))
        shown = kw.name.format(x=value) if kw.has_value else kw.name
        kw_list.insert("end", shown)
        update_preview()

    def remove_keyword():
        sel = kw_list.curselection()
        if sel:
            kw_list.delete(sel[0])
            chosen_keywords.pop(sel[0])
            update_preview()

    tk.Button(kw_frame, text="Add", command=add_keyword, bg=GOLD,
              relief="flat").pack(side="left", padx=6)
    tk.Button(kw_frame, text="Remove", command=remove_keyword, bg=NAVY2,
              fg=TEXT, relief="flat").pack(side="left")
    kw_list_hint = tk.Label(form, text="double-click a keyword to remove it",
                            bg=NAVY, fg="#5a648a", font=("Georgia", 8))
    kw_list_hint.grid(row=7, column=0, sticky="ne", pady=(8, 0))

    label("Ability text", 8)
    rules_box = tk.Text(form, height=4, width=44, bg=NAVY2, fg=TEXT,
                        insertbackground=TEXT, relief="flat", wrap="word")
    rules_box.grid(row=8, column=1, columnspan=2, sticky="we", padx=6,
                   pady=(8, 0))
    label("Flavor", 9)
    flavor_entry = entry(9)
    label("Set code", 10)
    set_entry = entry(10, width=10)
    set_entry.insert(0, "BASE")
    collectible_var = tk.BooleanVar(value=True)
    tk.Checkbutton(form, text="Collectible", variable=collectible_var,
                   bg=NAVY, fg=TEXT, selectcolor=NAVY2,
                   command=lambda: update_preview()).grid(
        row=10, column=2, sticky="w")

    def load_art(path: str):
        state["art_source"] = path
        state["offset"] = [0.0, 0.0]
        state["zoom"] = 1.0
        try:
            from PIL import Image
            state["art_img"] = Image.open(path).convert("RGB")
        except Exception as exc:      # noqa: BLE001
            messagebox.showerror("Art", f"Couldn't open that image: {exc}")
            state["art_img"] = None
        update_preview()

    label("Font sizes", 15)
    fs_frame = tk.Frame(form, bg=NAVY)
    fs_frame.grid(row=15, column=1, columnspan=2, sticky="w", padx=6,
                  pady=(8, 0))
    font_entries = {}
    for key in ("name", "type", "cost", "rules", "flavor", "stats"):
        tk.Label(fs_frame, text=key, bg=NAVY, fg=TEXT,
                 font=("Georgia", 8)).pack(side="left", padx=(6, 1))
        e = tk.Entry(fs_frame, width=4, bg=NAVY2, fg=TEXT,
                     insertbackground=TEXT, relief="flat")
        e.insert(0, str(DEFAULT_FONT_SIZES[key]))
        e.pack(side="left")
        font_entries[key] = e

    def font_sizes() -> dict:
        sizes = {}
        for key, widget in font_entries.items():
            try:
                sizes[key] = max(8, min(160, int(widget.get())))
            except ValueError:
                pass
        return sizes

    def choose_art():
        path = filedialog.askopenfilename(
            title="Choose card art",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if path:
            load_art(path)

    tk.Button(form, text="Choose Art...", command=choose_art, bg=NAVY2,
              fg=TEXT, relief="flat").grid(row=11, column=1, sticky="w",
                                           padx=6, pady=(12, 0))

    label("Service key (publish)", 12)
    key_entry = entry(12)
    key_entry.insert(0, os.environ.get("ARCANUM_SERVICE_KEY", ""))

    status = tk.Label(form, text="", bg=NAVY, fg=TEXT, wraplength=460,
                      justify="left")
    status.grid(row=14, column=0, columnspan=3, sticky="we", pady=(10, 0))

    # ---------------- right: live preview ----------------
    PREVIEW_W, PREVIEW_H = 380, 560
    preview = tk.Canvas(root, width=PREVIEW_W, height=PREVIEW_H, bg=NAVY2,
                        highlightthickness=0)
    preview.pack(side="right", padx=14, pady=12)
    art_photo = {"img": None}
    drag = {"active": False, "last": (0, 0)}

    def preview_scale() -> float:
        return PREVIEW_H / TEMPLATE_SIZE[1] if RENDER_OK else 1.0

    def in_art_window(px, py) -> bool:
        if not RENDER_OK:
            return False
        s = preview_scale()
        ox = (PREVIEW_W - TEMPLATE_SIZE[0] * s) / 2
        l, t, r, b = ART_CORE
        return (ox + l * s <= px <= ox + r * s) and (t * s <= py <= b * s)

    def on_press(ev):
        if in_art_window(ev.x, ev.y) and state["art_img"] is not None:
            drag["active"] = True
            drag["last"] = (ev.x, ev.y)
            preview.configure(cursor="fleur")

    def on_move(ev):
        if not drag["active"]:
            return
        s = preview_scale()
        dx = (ev.x - drag["last"][0]) / s
        dy = (ev.y - drag["last"][1]) / s
        drag["last"] = (ev.x, ev.y)
        state["offset"][0] += dx
        state["offset"][1] += dy
        update_preview()

    def on_release(_ev):
        drag["active"] = False
        preview.configure(cursor="")

    def on_wheel(ev):
        if state["art_img"] is None or not in_art_window(ev.x, ev.y):
            return
        step = 1.1 if getattr(ev, "delta", 0) > 0 or getattr(ev, "num", 0) == 4 \
            else 1 / 1.1
        state["zoom"] = max(0.4, min(4.0, state["zoom"] * step))
        update_preview()

    preview.bind("<ButtonPress-1>", on_press)
    preview.bind("<B1-Motion>", on_move)
    preview.bind("<ButtonRelease-1>", on_release)
    preview.bind("<MouseWheel>", on_wheel)          # Windows/mac
    preview.bind("<Button-4>", on_wheel)            # Linux
    preview.bind("<Button-5>", on_wheel)

    # drag-and-drop a file onto the preview (optional tkinterdnd2)
    def register_dnd():
        try:
            from tkinterdnd2 import DND_FILES
            preview.drop_target_register(DND_FILES)

            def on_drop(ev):
                path = ev.data.strip("{}").split("} {")[0]
                load_art(path)
            preview.dnd_bind("<<Drop>>", on_drop)
            return True
        except Exception:  # noqa: BLE001
            return False

    valid_line = tk.Label(form, text="", bg=NAVY, fg="#7dd487",
                          font=("Georgia", 9, "bold"), anchor="w",
                          justify="left")
    valid_line.grid(row=11, column=0, columnspan=3, sticky="we", padx=2,
                    pady=(10, 0))
    composed_line = tk.Label(form, text="", bg=NAVY, fg="#9aa3b2",
                             font=("Georgia", 8), anchor="w",
                             justify="left", wraplength=430)
    composed_line.grid(row=12, column=0, columnspan=3, sticky="we", padx=2)

    def refresh_validation(spec: CardSpec) -> None:
        ok, why = spec.validate()
        if ok:
            valid_line.config(text="✓ Card is valid", fg="#7dd487")
        else:
            valid_line.config(text=f"✗ {why}", fg="#e58a8a")
        text = spec.composed_text()
        composed_line.config(
            text=("Players will read:  " + text) if text else "")

    def build_spec() -> CardSpec:
        def num(name):
            try:
                return int(stat_entries[name].get())
            except ValueError:
                return 0
        hero_types = [v.get() for v in ht_vars if v.get()]
        return CardSpec(
            id=id_entry.get().strip() or make_card_id(name_entry.get() or "card"),
            name=name_entry.get().strip() or "Unnamed",
            card_type=CardType(type_var.get()),
            rarity=Rarity(rarity_var.get()),
            cost=num("cost"), attack=num("attack"), health=num("health"),
            durability=num("durability"), hero_types=hero_types,
            keywords=list(chosen_keywords),
            rules_text=rules_box.get("1.0", "end").strip(),
            flavor=flavor_entry.get().strip(),
            image=resolve_image_name(
                id_entry.get().strip(),
                editing["loaded_id"], editing["image"],
                state["art_img"] is not None),
            set_code=set_entry.get().strip() or "BASE",
            collectible=collectible_var.get())

    RARITY_HEX = {"common": "#9aa3b2", "uncommon": "#4caf7d",
                  "rare": "#569cff", "epic": "#b06aff", "legendary": GOLD}

    def update_preview(*_a):
        spec = build_spec()
        refresh_validation(spec)
        _schedule_draft(spec)
        preview.delete("all")
        if RENDER_OK:
            try:
                from PIL import Image, ImageTk
                card = renderer_for(spec).render(
                    spec, state["art_img"],
                    offset=tuple(state["offset"]), zoom=state["zoom"],
                    font_sizes=font_sizes())
                s = preview_scale()
                card = card.resize((round(card.width * s),
                                    round(card.height * s)), Image.LANCZOS)
                art_photo["img"] = ImageTk.PhotoImage(card)
                preview.create_image(PREVIEW_W // 2, PREVIEW_H // 2,
                                     image=art_photo["img"])
                if state["art_img"] is None:
                    preview.create_text(PREVIEW_W // 2, PREVIEW_H // 2 - 60,
                                        text="drop art here\n(or Choose Art)",
                                        fill="#5a648a", justify="center",
                                        font=("Georgia", 10, "italic"))
                else:
                    preview.create_text(PREVIEW_W // 2, PREVIEW_H - 10,
                                        text="drag to reposition · wheel to zoom",
                                        fill="#5a648a", font=("Georgia", 8))
                return
            except Exception as exc:  # noqa: BLE001
                print("Template render failed:", exc)
        _vector_preview(spec)

    RARITY_HEX = {"common": "#9aa3b2", "uncommon": "#4caf7d",
                  "rare": "#569cff", "epic": "#b06aff", "legendary": GOLD}

    def _vector_preview(spec):
        edge = RARITY_HEX[spec.rarity.value]
        preview.create_rectangle(8, 8, 352, 512, outline=edge, width=3)
        preview.create_rectangle(14, 14, 346, 506, outline=GOLD, width=1)
        preview.create_oval(20, 20, 56, 56, fill="#569cff", outline=GOLD)
        preview.create_text(38, 38, text=str(spec.cost), fill="white",
                            font=("Georgia", 15, "bold"))
        preview.create_text(200, 38, text=spec.name, fill=GOLD,
                            font=("Georgia", 13, "bold"), width=260)
        preview.create_rectangle(24, 62, 336, 296, fill="#0a0f24",
                                 outline=edge)
        type_line = spec.card_type.value.title()
        if spec.hero_types:
            type_line += "  —  " + " / ".join(spec.hero_types)
        preview.create_text(180, 312, text=type_line, fill=TEXT,
                            font=("Georgia", 9))
        preview.create_rectangle(24, 326, 336, 452, outline=edge)
        preview.create_text(30, 332, text=spec.composed_text()[:700],
                            fill=TEXT, anchor="nw", width=300,
                            font=("Georgia", 8))
        if spec.card_type in (CardType.HERO, CardType.MINION):
            preview.create_text(322, 488, text=f"{spec.attack}/{spec.health}",
                                fill=GOLD, font=("Georgia", 14, "bold"))
        elif spec.card_type is CardType.CHAMPION:
            preview.create_text(316, 488, text=f"♥{spec.health}", fill=GOLD,
                                font=("Georgia", 14, "bold"))
        elif spec.card_type is CardType.BARRIER:
            preview.create_text(314, 488, text=f"⛨{spec.durability}",
                                fill=GOLD, font=("Georgia", 14, "bold"))

    for widget in (name_entry, id_entry, flavor_entry, set_entry,
                   *stat_entries.values(), *font_entries.values()):
        widget.bind("<KeyRelease>", update_preview)
    rules_box.bind("<KeyRelease>", update_preview)
    for var in ht_vars:
        var.trace_add("write", lambda *a: update_preview())
    rarity_var.trace_add("write", lambda *a: update_preview())
    type_var.trace_add("write", lambda *a: update_preview())

    def validate_first() -> CardSpec | None:
        spec = build_spec()
        if not id_entry.get().strip():
            id_entry.insert(0, spec.id)
        ok, why = spec.validate()
        if not ok:
            messagebox.showerror("Invalid card", why)
            return None
        return spec

    def rendered_card(spec) -> Path | None:
        """Final product: full composited card PNG (frame + art + text)."""
        if not RENDER_OK:
            return None
        out = work_dir() / f"_publish_{spec.id}.png"
        renderer_for(spec).render_png(spec, state["art_img"],
                                      offset=tuple(state["offset"]),
                                      zoom=state["zoom"],
                                      font_sizes=font_sizes(), out_path=out)
        return out

    draft_job = {"id": None}

    def _schedule_draft(spec: CardSpec) -> None:
        if draft_job["id"] is not None:
            root.after_cancel(draft_job["id"])
        payload = {"spec": spec.to_dict(),
                   "art_source": state["art_source"],
                   "offset": list(state["offset"]), "zoom": state["zoom"],
                   "editing": dict(editing)}
        draft_job["id"] = root.after(
            800, lambda: (save_draft(payload),
                          draft_job.update(id=None)))

    def load_spec_into_form(spec: CardSpec, image_name: str = "",
                            loaded_id: str = "") -> None:
        name_entry.delete(0, "end"); name_entry.insert(0, spec.name)
        id_entry.delete(0, "end"); id_entry.insert(0, spec.id)
        type_var.set(spec.card_type.value)
        rarity_var.set(spec.rarity.value)
        for stat, value in (("cost", spec.cost), ("attack", spec.attack),
                            ("health", spec.health),
                            ("durability", spec.durability)):
            stat_entries[stat].delete(0, "end")
            stat_entries[stat].insert(0, str(value))
        for i, var in enumerate(ht_vars):
            var.set(spec.hero_types[i] if i < len(spec.hero_types) else "")
        chosen_keywords.clear()
        kw_list.delete(0, "end")
        for ref in spec.keywords:
            kw = KEYWORDS_BY_ID.get(ref.id)
            if kw is None:
                continue
            chosen_keywords.append(KeywordRef(ref.id, ref.value))
            shown = kw.name.format(x=ref.value) if kw.has_value else kw.name
            kw_list.insert("end", shown)
        rules_box.delete("1.0", "end")
        rules_box.insert("1.0", spec.rules_text)
        flavor_entry.delete(0, "end"); flavor_entry.insert(0, spec.flavor)
        set_entry.delete(0, "end")
        set_entry.insert(0, spec.set_code or "BASE")
        collectible_var.set(spec.collectible)
        editing["loaded_id"] = loaded_id
        editing["image"] = image_name
        refresh_kw_options()
        apply_type_gating()
        update_preview()

    def browse_database():
        key = key_entry.get().strip()
        ok, result = fetch_cards(key)
        if not ok:
            status.config(text=result, fg="#e58a8a")
            return
        rows = result
        win = tk.Toplevel(root)
        win.title(f"Card Database — {len(rows)} cards")
        win.configure(bg=NAVY)
        win.geometry("460x520")
        search_var = tk.StringVar()
        tk.Entry(win, textvariable=search_var, bg=NAVY2, fg=TEXT,
                 insertbackground=TEXT, relief="flat").pack(
            fill="x", padx=10, pady=(10, 4))
        listing = tk.Listbox(win, bg=NAVY2, fg=TEXT, selectbackground=GOLD,
                             relief="flat", font=("Consolas", 10))
        listing.pack(fill="both", expand=True, padx=10, pady=4)
        shown_rows = []

        def repopulate(*_a):
            needle = search_var.get().lower()
            listing.delete(0, "end")
            shown_rows.clear()
            for row in rows:
                data = row.get("data") or {}
                ct = str(data.get("card_type", "?"))
                line = f"{row.get('name', '?'):<28} {ct:<9} {row.get('id')}"
                if needle and needle not in line.lower():
                    continue
                shown_rows.append(row)
                listing.insert("end", line)
        search_var.trace_add("write", repopulate)
        repopulate()

        def load_selected(_e=None):
            sel = listing.curselection()
            if not sel:
                return
            row = shown_rows[sel[0]]
            try:
                spec, image_name = spec_from_row(row)
            except Exception as exc:  # noqa: BLE001
                status.config(text=f"Couldn't load that card: {exc}",
                              fg="#e58a8a")
                return
            load_spec_into_form(spec, image_name, loaded_id=spec.id)
            status.config(
                text=f"Editing '{spec.name}' — its art is kept unless you "
                     "load new art; changing the ID publishes a copy.",
                fg="#e5c98a")
            win.destroy()
        listing.bind("<Double-Button-1>", load_selected)
        tk.Button(win, text="Load selected", command=load_selected, bg=GOLD,
                  relief="flat", padx=12, pady=4).pack(pady=(0, 10))

    def duplicate_card():
        base = name_entry.get().strip() or "card"
        id_entry.delete(0, "end")
        id_entry.insert(0, make_card_id(base + " copy"))
        editing["loaded_id"] = ""
        editing["image"] = ""
        status.config(text="Duplicated — publishing creates a new card.",
                      fg=TEXT)
        update_preview()

    def restore_draft_if_any():
        payload = load_draft()
        if not payload:
            return
        try:
            spec = CardSpec.from_dict(payload.get("spec") or {})
        except Exception:  # noqa: BLE001
            return
        if not (spec.name.strip() and spec.name != "Unnamed") \
                and not payload.get("art_source"):
            return
        load_spec_into_form(spec,
                            payload.get("editing", {}).get("image", ""),
                            payload.get("editing", {}).get("loaded_id", ""))
        art = payload.get("art_source")
        if art and Path(art).exists():
            load_art(art)
            state["offset"] = list(payload.get("offset", [0.0, 0.0]))
            state["zoom"] = float(payload.get("zoom", 1.0))
            update_preview()
        status.config(text="Draft restored from your last session.",
                      fg=TEXT)

    def do_publish():
        spec = validate_first()
        if spec is None:
            return
        key = key_entry.get().strip()
        ok, message = publish(spec, rendered_card(spec), key)
        status.config(text=message, fg="#7dd487" if ok else "#e58a8a")
        if ok:
            # the published card is now "the existing card": further edits
            # without new art keep its (possibly cache-busted) image name
            editing["loaded_id"] = spec.id
            editing["image"] = spec.image
            try:
                (work_dir() / DRAFT_PATH_NAME).unlink(missing_ok=True)
            except OSError:
                pass

    def do_save_local():
        spec = validate_first()
        if spec is None:
            return
        status.config(text=save_local(spec, rendered_card(spec)),
                      fg="#7dd487")

    def do_new_card():
        """Reset the form to a blank card. The service key field is left
        alone on purpose — it's the designer's own secret, not part of the
        card being designed, and re-pasting it for every card would be
        tedious (and easy to fat-finger wrong)."""
        name_entry.delete(0, "end")
        id_entry.delete(0, "end")
        type_var.set("hero")
        rarity_var.set("common")
        for e in stat_entries.values():
            e.delete(0, "end")
            e.insert(0, "0")
        for var in ht_vars:
            var.set("")
        chosen_keywords.clear()
        kw_list.delete(0, "end")
        refresh_kw_options()
        rules_box.delete("1.0", "end")
        flavor_entry.delete(0, "end")
        set_entry.delete(0, "end")
        set_entry.insert(0, "BASE")
        collectible_var.set(True)
        for key, e in font_entries.items():
            e.delete(0, "end")
            e.insert(0, str(DEFAULT_FONT_SIZES[key]))
        state["art_source"] = None
        state["art_img"] = None
        state["offset"] = [0.0, 0.0]
        state["zoom"] = 1.0
        editing["loaded_id"] = ""
        editing["image"] = ""
        status.config(text="New card — form cleared.", fg=TEXT)
        apply_type_gating()
        update_preview()

    buttons = tk.Frame(form, bg=NAVY)
    buttons.grid(row=13, column=0, columnspan=3, pady=(14, 0), sticky="w")
    tk.Button(buttons, text="Publish to Database", command=do_publish,
              bg=GOLD, font=("Georgia", 11, "bold"),
              relief="flat", padx=14, pady=6).pack(side="left")
    tk.Button(buttons, text="Save Local", command=do_save_local, bg=NAVY2,
              fg=TEXT, relief="flat", padx=14, pady=6).pack(side="left",
                                                            padx=10)
    tk.Button(buttons, text="New Card", command=do_new_card, bg=NAVY2,
              fg=TEXT, relief="flat", padx=14, pady=6).pack(side="left")
    tk.Button(buttons, text="Browse DB…", command=browse_database, bg=NAVY2,
              fg=TEXT, relief="flat", padx=14, pady=6).pack(side="left",
                                                            padx=10)
    tk.Button(buttons, text="Duplicate", command=duplicate_card, bg=NAVY2,
              fg=TEXT, relief="flat", padx=14, pady=6).pack(side="left")

    if dnd_available:
        register_dnd()
    else:
        status.config(text="Tip: pip install tkinterdnd2 to enable "
                           "drag-and-drop art.", fg=TEXT)
    apply_type_gating()
    restore_draft_if_any()
    update_preview()
    root.mainloop()


if __name__ == "__main__":
    run()
