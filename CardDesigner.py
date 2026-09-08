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

Requires: pip install pillow   (for art fitting)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error as _urlerr
from urllib import request as _urlreq

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arcanum.core.constants import SUPABASE_ANON_KEY, SUPABASE_URL  # noqa: E402
from arcanum.game.cardspec import (CardSpec, KeywordRef, make_card_id)  # noqa: E402
from arcanum.game.keywords import (HERO_TYPES, KEYWORDS_BY_ID, CardType,  # noqa: E402
                                   Rarity, keywords_for)

ART_BOX = (512, 384)          # card frame art window (4:3)
LOCAL_FILE = Path("data/designed_cards.json")
LOCAL_ART = Path("assets/cards")


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
        object_name = f"{spec.id}.png"
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
            if exc.code == 404:
                return False, ("Card saved, but the 'card-art' storage "
                               "bucket doesn't exist yet — create it in "
                               "Supabase -> Storage.")
            return False, f"Card saved, but art upload failed ({exc.code})."
        except (_urlerr.URLError, TimeoutError, OSError) as exc:
            return False, f"Card saved, but art upload failed: {exc}"
    return True, f"Published '{spec.name}' ({spec.id}) to the card database."


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def run() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    NAVY, NAVY2, GOLD, TEXT = "#0d1530", "#16224a", "#d4af37", "#e8e6df"

    root = tk.Tk()
    root.title("Arcanum Card Designer")
    root.configure(bg=NAVY)
    root.geometry("1180x760")

    state = {"art_source": None, "art_fitted": None}

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

    label("Cost / Attack / Health / Durability", 4)
    stat_frame = tk.Frame(form, bg=NAVY)
    stat_frame.grid(row=4, column=1, sticky="w", padx=6, pady=(8, 0))
    stat_entries = {}
    for stat in ("cost", "attack", "health", "durability"):
        e = tk.Entry(stat_frame, width=5, bg=NAVY2, fg=TEXT,
                     insertbackground=TEXT, relief="flat")
        e.insert(0, "0")
        e.pack(side="left", padx=3)
        stat_entries[stat] = e

    label("Hero types (up to 2)", 5)
    ht_frame = tk.Frame(form, bg=NAVY)
    ht_frame.grid(row=5, column=1, sticky="w", padx=6, pady=(8, 0))
    ht_vars = [tk.StringVar(value=""), tk.StringVar(value="")]
    for var in ht_vars:
        ttk.Combobox(ht_frame, textvariable=var, state="readonly",
                     values=[""] + list(HERO_TYPES), width=16).pack(
            side="left", padx=3)

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
    chosen_keywords: list[KeywordRef] = []

    def refresh_kw_options(*_a):
        try:
            ct = CardType(type_var.get())
        except ValueError:
            return
        options = [k.id for k in keywords_for(ct)]
        kw_pick["values"] = options
        kw_pick.set(options[0] if options else "")
    type_box.bind("<<ComboboxSelected>>", refresh_kw_options)
    refresh_kw_options()

    def add_keyword():
        kw_id = kw_pick.get()
        if not kw_id:
            return
        kw = KEYWORDS_BY_ID[kw_id]
        value = None
        if kw.has_value:
            try:
                value = int(kw_value.get())
            except ValueError:
                messagebox.showerror("Keyword", f"{kw.name} needs a number.")
                return
        chosen_keywords.append(KeywordRef(kw_id, value))
        shown = kw.name.format(x=value) if kw.has_value else kw.name
        tag = "" if kw.implemented else "   [engine v2]"
        kw_list.insert("end", shown + tag)
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

    def choose_art():
        path = filedialog.askopenfilename(
            title="Choose card art",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not path:
            return
        state["art_source"] = path
        fitted = Path("data") / "_designer_art.png"
        fitted.parent.mkdir(exist_ok=True)
        fit_art(path, fitted)
        state["art_fitted"] = fitted
        update_preview()

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
    preview = tk.Canvas(root, width=360, height=520, bg=NAVY2,
                        highlightthickness=0)
    preview.pack(side="right", padx=14, pady=12)
    art_photo = {"img": None}

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
            image=f"{id_entry.get().strip()}.png" if state["art_fitted"] else "",
            set_code=set_entry.get().strip() or "BASE",
            collectible=collectible_var.get())

    RARITY_HEX = {"common": "#9aa3b2", "uncommon": "#4caf7d",
                  "rare": "#569cff", "mythic": GOLD}

    def update_preview(*_a):
        spec = build_spec()
        preview.delete("all")
        edge = RARITY_HEX[spec.rarity.value]
        preview.create_rectangle(8, 8, 352, 512, outline=edge, width=3)
        preview.create_rectangle(14, 14, 346, 506, outline=GOLD, width=1)
        # cost gem + name
        preview.create_oval(20, 20, 56, 56, fill="#569cff", outline=GOLD)
        preview.create_text(38, 38, text=str(spec.cost), fill="white",
                            font=("Georgia", 15, "bold"))
        preview.create_text(200, 38, text=spec.name, fill=GOLD,
                            font=("Georgia", 13, "bold"), width=260)
        # art window
        if state["art_fitted"] and state["art_fitted"].exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(state["art_fitted"]).resize((312, 234))
                art_photo["img"] = ImageTk.PhotoImage(img)
                preview.create_image(24, 62, image=art_photo["img"],
                                     anchor="nw")
            except Exception:  # noqa: BLE001
                preview.create_rectangle(24, 62, 336, 296, fill="#0a0f24")
        else:
            preview.create_rectangle(24, 62, 336, 296, fill="#0a0f24",
                                     outline=edge)
            preview.create_text(180, 179, text="( art )", fill="#5a648a")
        # type line
        type_line = spec.card_type.value.title()
        if spec.hero_types:
            type_line += "  —  " + " / ".join(spec.hero_types)
        type_line += f"   ·   {spec.rarity.value.title()}"
        preview.create_text(180, 312, text=type_line, fill=TEXT,
                            font=("Georgia", 9))
        # rules box
        preview.create_rectangle(24, 326, 336, 452, outline=edge)
        preview.create_text(30, 332, text=spec.composed_text()[:700],
                            fill=TEXT, anchor="nw", width=300,
                            font=("Georgia", 8))
        if spec.flavor:
            preview.create_text(180, 466, text=f"“{spec.flavor[:80]}”",
                                fill="#8b93ad", font=("Georgia", 8, "italic"),
                                width=300)
        # stats
        if spec.card_type in (CardType.HERO, CardType.MINION):
            preview.create_text(322, 488, text=f"{spec.attack}/{spec.health}",
                                fill=GOLD, font=("Georgia", 14, "bold"))
        elif spec.card_type is CardType.CHAMPION:
            preview.create_text(316, 488, text=f"♥{spec.health}", fill=GOLD,
                                font=("Georgia", 14, "bold"))
        elif spec.card_type is CardType.BARRIER:
            preview.create_text(314, 488, text=f"⛨{spec.durability}",
                                fill=GOLD, font=("Georgia", 14, "bold"))
        preview.create_text(48, 490, text=spec.set_code, fill="#5a648a",
                            font=("Georgia", 8))

    for widget in (name_entry, id_entry, flavor_entry, set_entry,
                   *stat_entries.values()):
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

    def do_publish():
        spec = validate_first()
        if spec is None:
            return
        key = key_entry.get().strip()
        ok, message = publish(spec, state["art_fitted"], key)
        status.config(text=message, fg="#7dd487" if ok else "#e58a8a")

    def do_save_local():
        spec = validate_first()
        if spec is None:
            return
        status.config(text=save_local(spec, state["art_fitted"]),
                      fg="#7dd487")

    buttons = tk.Frame(form, bg=NAVY)
    buttons.grid(row=13, column=0, columnspan=3, pady=(14, 0), sticky="w")
    tk.Button(buttons, text="Publish to Database", command=do_publish,
              bg=GOLD, font=("Georgia", 11, "bold"),
              relief="flat", padx=14, pady=6).pack(side="left")
    tk.Button(buttons, text="Save Local", command=do_save_local, bg=NAVY2,
              fg=TEXT, relief="flat", padx=14, pady=6).pack(side="left",
                                                            padx=10)

    update_preview()
    root.mainloop()


if __name__ == "__main__":
    run()
