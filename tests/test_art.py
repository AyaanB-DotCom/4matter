import asyncio

from fourmatter.app import FourMatterApp
from fourmatter.demo.fixtures import DemoBackend
from fourmatter.widgets.art_panel import ArtPanel, downsample, load_art, render_art


def test_art_is_packaged_and_rectangular():
    lines, width = load_art()
    assert (len(lines), width) == (75, 200) and all(len(ln) == width for ln in lines)


def test_downsample_fits_keeps_proportions_and_never_enlarges():
    lines, width = load_art()
    g = downsample(lines, width, 40, 20)
    assert len(g[0]) <= 40 and len(g) <= 20 and abs(len(g[0]) / len(g) - 200 / 75) < 0.5
    big = downsample(lines, width, 500, 500)
    assert (len(big[0]), len(big)) == (200, 75)
    assert all(0 <= v <= 1 for r in g for v in r)


def test_render_uses_ramp_when_scaled_and_original_when_not():
    scaled = render_art(40, 20)
    assert all(len(t) == len(n) for t, n in scaled) and set("".join(t for t, _ in scaled)) <= set(" .:-=+*#%@")
    assert render_art(300, 100)[0][0] == load_art()[0][0]


def test_g_opens_full_screen_art_at_any_size():
    from fourmatter.screens.art import ArtScreen

    async def go():
        app = FourMatterApp(DemoBackend(os_name="linux"))
        async with app.run_test(size=(80, 24)) as p:
            await p.pause(0.3)
            await p.press("g")
            await p.pause(0.2)
            assert isinstance(app.screen, ArtScreen) and app.screen.query_one(ArtPanel).size.width > 60
            await p.press("escape")
            await p.pause(0.1)
            assert not isinstance(app.screen, ArtScreen)
    asyncio.run(go())


def test_panel_hidden_on_narrow_and_shown_on_wide_terminals():
    async def visible(size):
        app = FourMatterApp(DemoBackend(os_name="linux"))
        async with app.run_test(size=size) as p:
            await p.pause(0.4)
            panel = app.screen.query_one(ArtPanel)
            return panel.display, panel.size.width
    assert asyncio.run(visible((80, 24)))[0] is False
    shown, w = asyncio.run(visible((140, 40)))
    assert shown and w >= 30
