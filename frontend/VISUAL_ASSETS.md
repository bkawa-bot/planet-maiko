# Visual Assets Needed

Assets referenced by the scene engine (`src/planet_maiko/brain/creativity/scene.py`) that need pixel-art illustrations. All assets should match the existing SVG world style: 160x90 viewBox, crisp-edges rendering, sherbet palette.

## Weather Overlays
Layered on top of the world SVG backgrounds.

| Asset | Trigger | Notes |
|-------|---------|-------|
| rain_light | rain + temp >= 50F | Sparse diagonal lines, semi-transparent |
| rain_heavy | rain + temp < 50F | Dense diagonal lines with splash effects |
| snow_light | snow + temp >= 25F | Gentle scattered pixel flakes |
| snow_heavy | snow + temp < 25F | Dense flakes, white ground accumulation |
| fog_thick | fog (any) | Horizontal semi-transparent bands |
| clouds_overcast | cloudy (any) | Gray cloud layer across top third |

## Maiko Outfits
Pixel art Maiko (shiba) sprite variants. Base sprite exists; need accessory variants.

| Outfit | Trigger | Notes |
|--------|---------|-------|
| default | fallback | Base shiba, no accessories |
| witch_hat | Halloween (Oct 25-31) | Pointy hat, maybe tiny broom |
| santa_hat | Christmas (Dec 15-31) | Red hat with white pom |
| party_hat | New Year (Jan 1-3) / Independence Day (Jul 1-4) | Colorful cone hat |
| bow_tie | Valentine's (Feb 10-14) | Pink/red bow tie |
| red_envelope | Lunar New Year (Jan 22-Feb 10) | Holding red envelope (hongbao) |
| clover_hat | St. Patrick's (Mar 14-17) | Green clover/shamrock hat |
| rainbow_scarf | Pride (Jun 1-30) | Rainbow-striped scarf |
| flower_crown | Day of the Dead (Oct 31-Nov 2) / Spring | Marigold wreath (DotD) or spring flowers |
| umbrella | Rain weather | Holding small umbrella |
| scarf | Snow/fog/winter/Thanksgiving | Cozy knit scarf |
| sunglasses | Summer season | Cool shades |
| leaf_crown | Autumn season | Orange/red leaf wreath |
| sleeping | Night time | Zzz, curled up pose |

## Holiday Decorations
Decorative elements scattered on the world scene during holidays.

| Asset | Trigger | Notes |
|-------|---------|-------|
| pumpkins | Halloween | 2-3 small pumpkins on front hill |
| bats | Halloween | 2-3 flying bat silhouettes in sky |
| ghosts | Halloween | 1-2 small translucent ghosts |
| christmas_tree | Christmas | Lit tree on mid hill |
| lights | Christmas | String of colored lights along hills |
| snowman | Christmas | Small snowman on front hill |
| hearts | Valentine's | Floating heart particles in sky |
| fireworks | New Year / Lunar New Year / Independence Day | Burst patterns in upper sky |
| lanterns | Lunar New Year (Jan 22-Feb 10) | Red/gold paper lanterns hung across scene |
| dragon | Lunar New Year (Jan 22-Feb 10) | Small dragon parade sprite on front hill |
| shamrocks | St. Patrick's (Mar 14-17) | Scattered shamrock/clover sprites on hills |
| rainbow | St. Patrick's (Mar 14-17) | Rainbow arc across upper sky |
| rainbow_flags | Pride (Jun 1-30) | Small rainbow flags planted on hills |
| confetti | Pride (Jun 1-30) | Colorful confetti particles drifting down |
| flags | Independence Day (Jul 1-4) | Small festive flags/bunting on hills |
| marigolds | Day of the Dead (Oct 31-Nov 2) | Orange marigold flowers scattered on hills |
| candles | Day of the Dead (Oct 31-Nov 2) | Small flickering candles on front hill |
| skulls | Day of the Dead (Oct 31-Nov 2) | Decorated sugar skull sprites (colorful, not scary) |
| cornucopia | Thanksgiving (Nov 22-28) | Small cornucopia on front hill |
| autumn_leaves | Thanksgiving (Nov 22-28) | Extra autumn leaf sprites drifting down |

## Season Specials
Decorative elements that appear based on season.

| Asset | Trigger | Notes |
|-------|---------|-------|
| flowers_spring | Spring | Scattered flowers on hills (more than day default) |
| butterflies | Spring | 2-3 animated butterfly sprites |
| fireflies | Summer + dusk/night | Already exists in world-night.svg, reuse pattern |
| falling_leaves | Autumn | Orange/red leaves drifting down |
| aurora | Winter + night + clear | Green/teal bands in sky (being added to world-night.svg) |

## Sky Variants
The scene engine generates these sky type strings. Currently handled by the 4 world SVGs + theme switching. Future work could swap SVGs dynamically.

| Sky Type | Current Handling | Notes |
|----------|-----------------|-------|
| dawn | Morning theme SVG | |
| clear_day | Day theme SVG | |
| clear_day_winter | Day theme SVG | Could add snow-covered hills variant |
| dusk | Sunset theme SVG | |
| night_clear | Night theme SVG | Stars visible |
| night_cloudy | Night theme SVG | Could dim stars |
| overcast | Day theme SVG | Could add cloud overlay |
| stormy | Day theme SVG | Dark clouds + rain overlay |
| snow_day | Day theme SVG | Snow overlay + white hills |
| snow_night | Night theme SVG | Snow overlay + white hills |
| fog | Any theme SVG | Fog overlay |

## Priority Order
1. **Maiko outfits** (most visible, adds personality)
2. **Weather overlays** (rain/snow make scenes dynamic)
3. **Holiday decorations** (seasonal delight)
4. **Season specials** (subtle but charming)

## Format
- SVG preferred (matches existing assets)
- 160x90 viewBox for full scenes, smaller for individual sprites
- `shape-rendering="crispEdges"` for pixel art look
- Semi-transparent where noted (use opacity attribute)
- Animations via `<animate>` elements where appropriate (fireflies, twinkling, falling)
