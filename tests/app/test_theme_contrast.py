"""The window's colours are measured, not looked at (#87, Decision 8).

Pure Python, no node, always runs. It parses `app/static/app.css` for the two token blocks (the light
`:root` and `:root[data-theme="dark"]`), composites the 8-digit tokens over the surface they sit on, computes
the WCAG 2.x relative luminance and contrast ratio, and checks a DECLARED table of pairs
(foreground, background, under, minimum, why) in both themes. It also fails when a token is neither in a pair
nor declared decorative, when the two blocks do not define the same tokens, and when a colour literal is
written outside the two blocks (hex, rgb, hsl or a named colour; the sandbox frame `.mockup` is the one approved
exception). The page's real text is measured again, in a browser, by S20/S21 of
tests/app/smoke/panel_smoke.mjs.
"""
from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app" / "static"
CSS = (APP / "app.css").read_text()
JS = (APP / "app.js").read_text()

LIGHT_SELECTOR = ":root"
DARK_SELECTOR = ':root[data-theme="dark"]'
HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# --- a CSS reader just big enough for this file ---------------------------------------------------

def rules(css: str) -> list[tuple[str, str]]:
    """(selector, declarations) of every rule; `@media` is entered, `@keyframes` is one rule named by itself."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[tuple[str, str]] = []

    def walk(text: str) -> None:
        i = 0
        while i < len(text):
            brace = text.find("{", i)
            if brace < 0:
                return
            head = text[i:brace].strip()
            depth, j = 1, brace + 1
            while depth and j < len(text):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            body = text[brace + 1:j - 1]
            if head.startswith("@media"):
                walk(body)
            elif head.startswith("@keyframes"):
                out.append((head, " ".join(re.findall(r"\{([^{}]*)\}", body))))
            else:
                out.append((" ".join(head.split()), body))
            i = j

    walk(css)
    return out


def declared(body: str) -> dict[str, str]:
    return {m.group(1): " ".join(m.group(2).split()) for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);?", body)}


def token_blocks(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """The colour tokens (hex valued) of the light and the dark block, names without the `--`."""
    found: dict[str, dict[str, str]] = {LIGHT_SELECTOR: {}, DARK_SELECTOR: {}}
    for selector, body in rules(css):
        if selector in found:
            found[selector].update({k[2:]: v for k, v in declared(body).items() if HEX.match(v)})
    return found[LIGHT_SELECTOR], found[DARK_SELECTOR]


# --- WCAG 2.x ------------------------------------------------------------------------------------

def rgba(value: str) -> tuple[float, float, float, float]:
    h = value.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return r, g, b, (int(h[6:8], 16) / 255 if len(h) == 8 else 1.0)


def over(top: tuple, under: tuple) -> tuple[float, float, float, float]:
    a = top[3]
    return (*(top[i] * a + under[i] * (1 - a) for i in range(3)), 1.0)


def luminance(c: tuple) -> float:
    def lin(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])


def ratio(fg: tuple, bg: tuple) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- the declared pairs --------------------------------------------------------------------------
# (foreground token, background token, the token the background sits over or None, minimum, why)

VERDICTS = ("pass", "skip", "todo", "fail")
KINDS = ("k-spec", "k-model", "k-mock", "k-report", "k-pr")
GROUNDS = ("bg", "surface", "surface2")


def text_pairs() -> list[tuple]:
    pairs: list[tuple] = []
    for fg in ("fg", "dim"):
        pairs += [(fg, g, None, 4.5, f"{fg} text on {g}") for g in GROUNDS]
    pairs += [("hot-ink", g, None, 4.5, f"red text on {g}") for g in GROUNDS]
    pairs += [("hot-ink", "hot2", g, 4.5, f"the live tag's text on its wash over {g}") for g in GROUNDS]
    for word in VERDICTS:
        pairs += [(word, g, None, 4.5, f"the word {word} on {g}") for g in GROUNDS]
        pairs += [(word, f"{word}-bg", g, 4.5, f"the word {word} on its tint over {g}") for g in ("bg", "surface")]
    for kind in KINDS:
        pairs += [(kind, g, None, 4.5, f"{kind} text/glyph on {g}") for g in ("surface", "surface2")]
        pairs.append((kind, f"{kind}-bg", "surface", 4.5, f"{kind} glyph on its chip over surface"))
        pairs.append(("fg", f"{kind}-bg", "surface", 4.5, f"the open card's name on {kind}'s chip over surface"))
    pairs.append(("on-hot", "hot", None, 4.5, "the number on the live step's red fill"))
    pairs.append(("fail", "fail-bg", "bg", 4.5, "the error banner"))
    pairs.append(("fail", "k-model-bg", "surface", 4.5, "the problem line inside the key-idea block"))
    return pairs


def non_text_pairs() -> list[tuple]:
    pairs: list[tuple] = [("hot", "bg", None, 3.0, "the live dot")]
    pairs += [("edge", g, None, 3.0, f"control outline on {g}") for g in ("bg", "surface")]
    pairs += [("ring", g, None, 3.0, f"focus ring on {g}") for g in ("bg", "surface")]
    pairs += [(word, "bg", None, 3.0, f"the outline of a {word} mark") for word in VERDICTS]
    return pairs


DECORATIVE = {"rule", "soft", "shadow", "hot2"}   # hairlines, washes and the short shadow: never the only mark


def failures(tokens: dict[str, str], pairs: list[tuple], theme: str) -> list[str]:
    if not tokens:
        return [f"app.css defines no colour tokens for the {theme} theme"]
    bad = []
    for fg, bg, under, minimum, why in pairs:
        missing = [t for t in (fg, bg, under) if t is not None and t not in tokens]
        if missing:
            bad.append(f"{theme}: {why} ({fg} on {bg}): token {', '.join(missing)} is not defined")
            continue
        ground = rgba(tokens[bg])
        if under is not None:
            ground = over(ground, rgba(tokens[under]))
        elif ground[3] < 1:
            bad.append(f"{theme}: {why}: {bg} is translucent and the pair names nothing under it")
            continue
        got = ratio(rgba(tokens[fg]), ground)
        if got < minimum:
            bad.append(f"{theme}: {why}: {fg} {tokens[fg]} on {bg}{f' over {under}' if under else ''} "
                       f"{tokens[bg]} is {got:.2f}:1, needs {minimum}:1")
    return bad


# --- the tests -----------------------------------------------------------------------------------

def test_every_text_pair_of_the_light_theme_reaches_4_5_to_1():
    light, _ = token_blocks(CSS)
    assert failures(light, text_pairs(), "light") == []


def test_every_text_pair_of_the_dark_theme_reaches_4_5_to_1():
    _, dark = token_blocks(CSS)
    assert failures(dark, text_pairs(), "dark") == []


def test_every_non_text_pair_reaches_3_to_1_in_both_themes():
    light, dark = token_blocks(CSS)
    assert failures(light, non_text_pairs(), "light") + failures(dark, non_text_pairs(), "dark") == []


def test_the_two_themes_define_the_same_tokens():
    light, dark = token_blocks(CSS)
    assert light and dark, "app.css needs both a :root block and a :root[data-theme=\"dark\"] block of colour tokens"
    assert set(light) == set(dark), (f"only in light: {sorted(set(light) - set(dark))}; "
                                     f"only in dark: {sorted(set(dark) - set(light))}")


def test_every_colour_token_is_in_a_pair_or_declared_decorative():
    light, dark = token_blocks(CSS)
    assert light and dark, "app.css has no token blocks to judge"
    judged = {t for pair in text_pairs() + non_text_pairs() for t in pair[:3] if t} | DECORATIVE
    assert sorted((set(light) | set(dark)) - judged) == [], "a colour token that no contrast pair judges"


NAMED_COLOURS = re.compile(r"\b(?:white|black|red|green|blue|gr[ae]y|silver|orange|yellow|purple|pink|brown|navy|teal"
                           r"|maroon|cyan|magenta|lime|olive|aqua|gold|ivory|beige)\b", re.I)
# The one approved exception: the sandbox frame of an authored mockup keeps its own colours (design, minor choices).
NAMED_COLOUR_EXCEPTIONS = {".mockup"}


def named_colour_rules(css: str) -> list[str]:
    """The rules that write a named colour (`white`, `red`...) in a value, except the approved exceptions."""
    found = []
    for selector, body in rules(css):
        if selector in NAMED_COLOUR_EXCEPTIONS:
            continue
        # a mask's `black` is an alpha mask, not a colour the eye sees
        values = [decl.split(":", 1)[1] for decl in body.split(";") if ":" in decl and "mask" not in decl.split(":", 1)[0]]
        if any(NAMED_COLOURS.search(v) for v in values):
            found.append(selector)
    return found


def test_the_named_colour_check_flags_a_named_colour_and_lets_the_declared_exception_through():
    css = ".a { background: white; } .mockup { background: white; } .b { color: var(--fg); } .c { border: 1px solid Red; } .d { mask-image: linear-gradient(black, transparent); }"
    assert named_colour_rules(css) == [".a", ".c"]


def test_no_colour_literal_is_written_outside_the_two_token_blocks():
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
    outside = [f"{selector} {{ {' '.join(body.split())} }}" for selector, body in rules(CSS)
               if selector not in (LIGHT_SELECTOR, DARK_SELECTOR) and literal.search(body)]
    assert outside == [], "a rule carries a colour literal outside the token blocks"
    assert named_colour_rules(CSS) == [], "a rule writes a named colour outside the token blocks"
    assert literal.findall(re.sub(r"/\*.*?\*/", "", JS, flags=re.S)) == [], "app.js writes a colour literal"
    light, dark = token_blocks(CSS)
    assert light and dark, "the colours live in two token blocks, and app.css has none"


def test_the_dark_ground_is_the_graphite_the_owner_chose():
    # The owner's approval of 2026-11-18 ("B - Neutral with a green breath"): the literals are the contract.
    _, dark = token_blocks(CSS)
    assert {k: dark.get(k) for k in ("bg", "surface", "surface2", "rule")} == {
        "bg": "#131413", "surface": "#1b1d1c", "surface2": "#232625", "rule": "#2e3231"}


def test_the_contrast_function_agrees_with_published_ratios_and_flags_a_lowered_token():
    white, black = rgba("#ffffff"), rgba("#000000")
    assert round(ratio(black, white), 2) == 21.0
    assert round(ratio(rgba("#777777"), white), 2) == 4.48
    light, dark = token_blocks(CSS)
    assert light and dark and failures(light, text_pairs(), "light") == []   # the check passes on the real CSS...
    lowered = dict(light, dim="#9a9a9a")
    msgs = failures(lowered, text_pairs(), "light")                         # ...and fails on a lowered token
    assert msgs and any("dim" in m for m in msgs)
    lowered_css = re.sub(r"(--dim:\s*)#[0-9a-fA-F]{6}", r"\1#9a9a9a", CSS)
    assert lowered_css != CSS and token_blocks(lowered_css) != token_blocks(CSS)
    assert failures(token_blocks(lowered_css)[0], text_pairs(), "light") != []


def test_each_verdict_word_and_each_artefact_kind_has_its_own_colour_in_each_theme():
    for name, tokens in zip(("light", "dark"), token_blocks(CSS)):
        assert tokens, f"no {name} tokens"
        for group in (VERDICTS, KINDS):
            for a, b in combinations(group, 2):
                assert a in tokens and b in tokens, f"{name}: {a} or {b} is not defined"
                assert tokens[a] != tokens[b], f"{name}: {a} and {b} share {tokens[a]}"


RED_SELECTORS = (".wfp-step.live", ".wfp-step.flows", ".wfp-dot", ".wfp-tag.live", ".wfp-agent", "tr.sel",
                 ".wfp-close:hover", "@keyframes ping", "@keyframes flow", "@keyframes beat")


def test_red_is_named_only_by_the_selectors_of_the_live_phase_and_its_marks():
    naming = [(sel, body) for sel, body in rules(CSS) if re.search(r"var\(--(hot|hot2|hot-ink)\)", body)]
    assert naming, "no rule names the live red: the live phase would have no mark"
    stray = [sel for sel, _ in naming
             if not all(any(allowed in part for allowed in RED_SELECTORS) for part in sel.split(","))]
    assert stray == [], "red is used outside the live phase and its marks"


def test_the_reduced_motion_block_stops_every_animation_and_transition():
    blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\s*\{", CSS)
    assert len(blocks) == 1 and CSS.count("prefers-reduced-motion") == 1
    start = CSS.index("@media (prefers-reduced-motion: reduce)")
    inner = CSS[start:]
    universal = [body for sel, body in rules(inner) if sel.startswith("*")]
    assert universal, "the block has no universal rule"
    assert "animation: none !important" in universal[0] and "transition: none !important" in universal[0]
    packets = [body for sel, body in rules(inner) if ".diagram .packet" in sel]
    assert packets and "display: none" in packets[0], "the packets of the diagram are not hidden"


BEZIER = re.compile(r"cubic-bezier\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")


def bouncing_easings(text: str) -> list[str]:
    """Every easing written in `text` (comments left out) that overshoots its end value: a cubic-bezier whose y1 or
    y2 leaves 0..1 (or whose x leaves it, which CSS rejects), a `linear()` list or the word `spring`."""
    plain = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    bad = []
    for m in BEZIER.finditer(plain):
        x1, y1, x2, y2 = (float(v) for v in m.groups())
        if not (0 <= x1 <= 1 and 0 <= x2 <= 1 and 0 <= y1 <= 1 and 0 <= y2 <= 1):
            bad.append(m.group(0))
    return bad + re.findall(r"\blinear\([^)]*\)|\bspring\b", plain)


def test_no_easing_of_the_whole_stylesheet_overshoots():
    """witness the stylesheet and the script as served (ticket 130, Decision 6) - distinguishes a window whose every
    cubic-bezier keeps y1 and y2 inside 0..1 and that writes no `linear()` or spring curve, the round buttons of
    ticket 87 included, from one that keeps a rule with an overshoot such as `cubic-bezier(.3, 1.5, .5, 1)`."""
    assert bouncing_easings(CSS) == [], "a rule of app.css has an easing that overshoots"
    assert bouncing_easings(JS) == [], "app.js has an easing that overshoots"


def test_the_motion_uses_the_one_soft_easing_by_name():
    """witness `--ease` and the script's WAAPI calls - distinguishes the keyword `ease` (the curve
    cubic-bezier(.25, .1, .25, 1)) written once in the stylesheet and named by every rule of the motion and by the
    script, from a second curve written beside it in a rule of the motion or in app.js."""
    code = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
    assert re.search(r"--ease:\s*ease;", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)), "--ease is not the keyword ease"
    assert not re.search(r"\bEASE\b|cubic-bezier", code), "the script writes a curve of its own"
    assert [e.strip() for e in re.findall(r"easing:\s*([^,}]+)", code)] == ['"ease"'] * code.count("easing:") and code.count("easing:")
    motion = [body for sel, body in rules(CSS) if re.search(r"view-transition|details-content|draw-in", sel)]
    assert motion and not [b for b in motion if "cubic-bezier" in b or re.search(r"\blinear\(|spring", b)], \
        "a rule of the motion writes its own curve instead of var(--ease)"


def test_the_reduced_motion_block_reaches_the_pseudo_elements_of_folds_and_transitions():
    """witness the reduced-motion block - distinguishes a block that names `::details-content` and the three
    `::view-transition-*(*)` pseudo-elements with `animation: none !important` and `transition: none !important`
    (the universal `*` rule does not reach them) from one that stops only the elements."""
    inner = CSS[CSS.index("@media (prefers-reduced-motion: reduce)"):]
    named = [sel for sel, body in rules(inner) if "::details-content" in sel and "::view-transition-group(*)" in sel
             and "animation: none !important" in body and "transition: none !important" in body]
    assert named, "reduced motion does not stop the folds' height or the rows' view transition"
