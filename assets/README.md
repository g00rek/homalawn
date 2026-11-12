# Homa Lawn's icon

The icon and banner artwork (`homalawn-icon-source.png` and everything made from it here, and
`docs/images/banner.png`) was made for Homa Lawn by its author. © the Homa Lawn author, licensed
under the Apache License 2.0 with the rest of the repository.

Every file below is made from one picture by `make_icons.py`:

    uv run --with numpy --with scipy python assets/make_icons.py [--lawnchair lawnchair]

| File | What it is | How it is made |
|---|---|---|
| `homalawn-icon-source.png` | The original artwork (2026-10-15), 1254 x 1254, full bleed: a purple to pink to peach sunset sky, a pale sun upper right, a navy hill at the bottom, and in the middle a dark phone (rounded rectangle, white outline, one pink rounded "app" square near its top right corner). | Given. Every other file is made from it. |
| `homalawn-scene.png` | The picture with the phone taken out: the sky, sun and hill filled in under it. Only used for the phone's adaptive icon; kept here to look at. | The phone is the one large white ring (pixels whose darkest channel is over 200 and whose channels are within 40 of each other) with its inside filled. That area, 4 px wider, is filled row by row: the sky's color is carried across from where the same row shows sky; the sun is a circle fitted to the part of its rim that shows, and inside it the sun's color is carried the same way; the hill's ridge is a parabola fitted to the ridge within 250 px either side of the phone, and below it the hill's color is carried (from the nearest row that shows hill). A 1.5 px blur hides the row seams. |
| `homalawn-mark.png` | The bar mark at 256 px, white on transparency: only the phone's outline and its app square, upright. To look at; the bar uses the masks below. | As the masks below, outline and app square together. |
| `src/homalawn/shell/HomaLawn/assets/homalawn-outline-<size>.png`, `homalawn-app-<size>.png` | What the bar draws: the mark as two white masks, the outline and the app square, at 16, 32, 64, 128 and 256 px, in the same square so they stack exactly. | Drawn, not cut out: the phone's shape is measured from the source (outline box 389,216 to 861,1108, corner radius 70, app square 640,309 to 802,466 with corner radius 28) and redrawn 14/16 of the square tall, centered, with every edge on a whole pixel (8 times oversampled, then averaged). The outline is 1/16 of the square wide (1 px at 16 px, 2 px at 32 px): the source's own outline, 1/64 of the phone's height, would vanish at bar size. The app square keeps its size and corner, and stays at least one outline width clear of the outline (at 16 px: an 8 x 14 outline with a 3 x 3 square, 1 px from the top and right). `Mark.qml` paints each mask in its own color; the bar uses the theme's `accent` for the outline and `red` (the accent when the theme has no red) for the app square while a phone is connected, both `muted` otherwise, and the whole mark dimmed while the service is down. |
| `src/homalawn/shell/HomaLawn/assets/homalawn-icon.png` | The window header's picture, next to "Homa Lawn": the whole picture as a rounded square (corner radius 22 % of the side), 128 px. | The source shrunk with Lanczos and cut to the rounded square. |
| `make_icons.py` | The script that makes all of the above and, with `--lawnchair DIR`, the phone's launcher icon in a Lawnchair checkout. | |

The phone's launcher icon (`--lawnchair`, kept as `patches/0013-homar-app-icon.patch`) is an
adaptive icon that looks like the picture. The picture fills the middle 72 dp of the 108 dp
canvas (the part a launcher's mask shows). The background layer is `homalawn-scene.png` scaled
to that, its edge pixels repeated out to the canvas's edge (only seen while a launcher moves the
layers). The foreground is the phone cut out of the source, where the picture has it: inside it
the pixels are kept as they are, and on its outer edge each pixel is white with the opacity that
takes the filled-in scene to the source's pixel, so no sky rim is left. The phone reaches 29.6 dp
from the center, inside the 66 dp safe circle (the script refuses to write it otherwise). The
monochrome layer (themed icons) is the phone's outline and app square in white, drawn like the
bar mark with the outline 1/14 of the phone's height. The legacy square and round PNGs at every
density are the whole picture in 48 dp on a shape with a 2 dp margin (corner 20 % for the square,
a circle for the round one).

To refresh the patch: reset the Lawnchair clone to the commit in `patches/BASE`, apply 0001-0012 and commit them,
apply 0013, run the script with `--lawnchair lawnchair`, stage `res`, commit with 0013's message
and write `git format-patch -1` back into 0013.

**The mark is drawn upright and pixel-aligned in the files, not scaled by QML.** The bar draws the
smallest mask at least as large as it needs (16 px, or 32 px at scale 2), so it lands pixel for
pixel and the 1 px outline stays crisp.
