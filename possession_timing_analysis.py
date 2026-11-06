import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from possession_builder import build_segments, possession_table

# ============================================================
# 0. PATHS (relative to the script, so the project runs on any PC)
# ============================================================

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()  # fallback when running cell-by-cell (F9) in Spyder

DATA_DIR = os.path.join(BASE_DIR, "..", "csv_euroleague")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

SEASONS = [f'E{y}' for y in range(2015, 2026)]
MAX_SECONDS = 30  # beyond this the clock reading is not trustworthy (24 s shot clock)

# Colours shared with the HTML reports
GREEN = '#3E8E4F'
CORAL = '#C6564F'
GOLD = '#B8860B'
TEXT = '#16233F'
BG = '#FBFCEE'

# ============================================================
# 1. LOAD DATA AND BUILD POSSESSIONS
# ============================================================

pbp = pd.read_csv(
    os.path.join(DATA_DIR, "euroleague_play_by_play.csv"),
    usecols=['game_id', 'season_code', 'number_of_play', 'quarter', 'team_id', 'play_type', 'marker_time']
)
header = pd.read_csv(os.path.join(DATA_DIR, "euroleague_header.csv"))

pbp = pbp[pbp['season_code'].isin(SEASONS)].copy()
pbp = pbp.sort_values(['game_id', 'number_of_play']).reset_index(drop=True)

segments = build_segments(pbp, header)
possessions = possession_table(segments)

print(f"Possessions built: {len(possessions):,}")

# ============================================================
# 2. KEEP POSSESSIONS WITH A RELIABLE CLOCK READING
# We study two ways of getting the ball that start a half-court or transition possession
# under similar conditions: a defensive rebound and an inbound after an opponent basket.
# Steals are left out on purpose: they are almost always immediate fast breaks.
# ============================================================

scope = possessions[possessions['origin'].isin(['def_rebound', 'after_score'])].copy()

n_all = len(scope)
scope = scope[scope['seconds_to_first_action'].notna() & (scope['seconds_to_first_action'] <= MAX_SECONDS)].copy()
print(f"Possessions with a valid clock reading: {len(scope):,} of {n_all:,} ({len(scope) / n_all * 100:.1f}%)")

# Exclude possessions where the clock forces the pace:
#  - the last 24 seconds of any period (the team has to shoot before time runs out)
#  - the last 2 minutes of the 4th quarter and overtime (intentional fouls, clock management)
n_valid = len(scope)
late_period = scope['start_clock'] <= 24
late_game = scope['quarter'].isin(['q4', 'extra_time']) & (scope['start_clock'] <= 120)
scope = scope[~(late_period | late_game)].copy()
print(f"After removing end-of-period and end-of-game possessions: {len(scope):,} of {n_valid:,}")

# ============================================================
# 3. SECONDS UNTIL THE FIRST ACTION (shot, free throw or turnover)
# ============================================================

bins = [-1, 5, 10, 15, 20, MAX_SECONDS]
bucket_labels = ['0-5 s', '6-10 s', '11-15 s', '16-20 s', '21+ s']
scope['bucket'] = pd.cut(scope['seconds_to_first_action'], bins=bins, labels=bucket_labels)

print("\n=== Seconds until the first action, after a defensive rebound ===")
print(scope[scope['origin'] == 'def_rebound']['seconds_to_first_action'].describe().round(1))

# What the first action is: turnover, free throws, three-pointer or two-pointer
# (layups and dunks are logged as two-pointers in these seasons)
def classify_first_action(play):
    if play == 'TO':
        return 'Turnover'
    if play in ('FTM', 'FTA'):
        return 'Free throws'
    if play in ('3FGM', '3FGA', '3FGAB'):
        return '3-pointer'
    return '2-pointer'

scope['first_action_type'] = scope['first_action_play'].map(classify_first_action)

# ============================================================
# 4. POINTS PER POSSESSION BY SPEED OF THE FIRST ACTION
# ============================================================

def summarise(data):
    out = data.groupby('bucket', observed=True)['possession_points'].agg(['count', 'mean', 'std'])
    out = out.rename(columns={'count': 'n', 'mean': 'ppp'})
    out['share'] = out['n'] / out['n'].sum() * 100
    out['ci95'] = 1.96 * out['std'] / np.sqrt(out['n'])
    return out

result = {origin: summarise(scope[scope['origin'] == origin]) for origin in ['def_rebound', 'after_score']}

for origin, table in result.items():
    print(f"\n=== Points per possession by speed of the first action: {origin} ===")
    print(table[['n', 'share', 'ppp', 'ci95']].round(3))

# ============================================================
# 5. WHAT HAPPENS FIRST, BY SPEED (defensive rebounds)
# ============================================================

dreb = scope[scope['origin'] == 'def_rebound']
mix = pd.crosstab(dreb['bucket'], dreb['first_action_type'], normalize='index') * 100
mix = mix[['2-pointer', '3-pointer', 'Free throws', 'Turnover']]
print("\n=== First action after a defensive rebound, by speed (% of possessions) ===")
print(mix.round(1))

# Points per possession within each type of first action, by speed: is the early advantage
# only a matter of taking different shots?
by_type = dreb.groupby(['first_action_type', 'bucket'], observed=True)['possession_points'].agg(['count', 'mean']).round(3)
print("\n=== Points per possession by first action type and speed (defensive rebounds) ===")
print(by_type.unstack('bucket')['mean'])

# ============================================================
# 6. STABILITY ACROSS SEASONS: fast (0-10 s) vs slow (16+ s)
# ============================================================

dreb = dreb.assign(speed=np.where(dreb['seconds_to_first_action'] <= 10, 'fast',
                                  np.where(dreb['seconds_to_first_action'] >= 16, 'slow', 'middle')))
speed_season = dreb[dreb['speed'] != 'middle'].groupby(['season_code', 'speed'])['possession_points'].mean().unstack()
speed_season['fast_minus_slow'] = speed_season['fast'] - speed_season['slow']
print("\n=== Fast (0-10 s) minus slow (16+ s) points per possession, by season ===")
print(speed_season.round(3))

# ============================================================
# 7. PLOT 1: POINTS PER POSSESSION BY SPEED OF THE FIRST ACTION
# ============================================================

fig_speed, ax = plt.subplots(figsize=(8.5, 5))
fig_speed.patch.set_facecolor(BG)
ax.set_facecolor(BG)

x = np.arange(len(bucket_labels))
width = 0.38
dr = result['def_rebound'].reindex(bucket_labels)
asc = result['after_score'].reindex(bucket_labels)

bars1 = ax.bar(x - width / 2, dr['ppp'], width, yerr=dr['ci95'], color=GREEN, capsize=3, ecolor=TEXT,
               label='After a defensive rebound')
bars2 = ax.bar(x + width / 2, asc['ppp'], width, yerr=asc['ci95'], color=GOLD, capsize=3, ecolor=TEXT,
               label='After an opponent basket')

ax.set_xticks(x)
ax.set_xticklabels(bucket_labels, color=TEXT)
ax.set_xlabel('Seconds from getting the ball to the first shot, free throw or turnover', color=TEXT)
ax.set_ylabel('Points per possession', color=TEXT)
ax.set_title('Does attacking early pay off?', color=TEXT)
ax.set_ylim(0.8, 1.45)
ax.tick_params(colors=TEXT)
for spine in ['top', 'right']:
    ax.spines[spine].set_visible(False)
for bars in (bars1, bars2):
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width() / 2, 0.82, f'{bar.get_height():.2f}', ha='center',
                color=BG, fontweight='bold', fontsize=10)
legend = ax.legend(loc='upper right', facecolor=BG, edgecolor=TEXT)
plt.setp(legend.get_texts(), color=TEXT)

plt.tight_layout()
fig_speed.savefig(os.path.join(IMAGES_DIR, 'points_by_speed_of_attack.png'), dpi=150, facecolor=fig_speed.get_facecolor())
plt.show()

# ============================================================
# 8. PLOT 2: WHAT THE FIRST ACTION IS, BY SPEED (defensive rebounds)
# ============================================================

mix_colors = {
    '2-pointer': GREEN,
    '3-pointer': GOLD,
    'Free throws': '#9AA6B8',
    'Turnover': CORAL,
}

fig_mix, ax = plt.subplots(figsize=(8.5, 4.8))
fig_mix.patch.set_facecolor(BG)
ax.set_facecolor(BG)

bottom = np.zeros(len(bucket_labels))
for col in mix.columns:
    values = mix.reindex(bucket_labels)[col].values
    ax.bar(bucket_labels, values, bottom=bottom, color=mix_colors[col], label=col, width=0.6)
    bottom += values

ax.set_xlabel('Seconds from the defensive rebound to the first action', color=TEXT)
ax.set_ylabel('% of possessions', color=TEXT)
ax.set_title('What the first action is, by how early it comes', color=TEXT)
ax.set_ylim(0, 100)
ax.tick_params(colors=TEXT)
for spine in ['top', 'right']:
    ax.spines[spine].set_visible(False)
legend = ax.legend(loc='center left', bbox_to_anchor=(1.01, 0.5), facecolor=BG, edgecolor=TEXT)
plt.setp(legend.get_texts(), color=TEXT)

plt.tight_layout()
fig_mix.savefig(os.path.join(IMAGES_DIR, 'first_action_by_speed.png'), dpi=150, facecolor=fig_mix.get_facecolor(), bbox_inches='tight')
plt.show()
