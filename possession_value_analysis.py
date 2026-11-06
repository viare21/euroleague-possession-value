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

# Colours shared with the HTML reports
GREEN = '#3E8E4F'
CORAL = '#C6564F'
GOLD = '#B8860B'
TEXT = '#16233F'
BG = '#FBFCEE'

# ============================================================
# 1. LOAD DATA
# ============================================================

pbp = pd.read_csv(
    os.path.join(DATA_DIR, "euroleague_play_by_play.csv"),
    usecols=['game_id', 'season_code', 'number_of_play', 'quarter', 'team_id', 'play_type', 'marker_time']
)
header = pd.read_csv(os.path.join(DATA_DIR, "euroleague_header.csv"))

pbp = pbp[pbp['season_code'].isin(SEASONS)].copy()
pbp = pbp.sort_values(['game_id', 'number_of_play']).reset_index(drop=True)

print(f"Play-by-play events in scope (E2015-E2025): {len(pbp):,}")
print(f"Games: {pbp['game_id'].nunique():,}")

# ============================================================
# 2. SPLIT EVERY GAME INTO POSSESSIONS (see possession_builder.py for the rules)
# ============================================================

segments = build_segments(pbp, header)
possessions = possession_table(segments)

print(f"\nSegments built: {len(segments):,}")
print(f"Possessions (a possession includes its second chances): {len(possessions):,}")

# ============================================================
# 3. VALIDATION
# ============================================================

# 3.1 Points rebuilt from the play-by-play must match the official final score
rebuilt = segments.groupby(['game_id', 'team_id'])['points'].sum().rename('rebuilt_points').reset_index()

official = pd.concat([
    header[['game_id', 'team_id_a', 'score_a']].rename(columns={'team_id_a': 'team_id', 'score_a': 'official_points'}),
    header[['game_id', 'team_id_b', 'score_b']].rename(columns={'team_id_b': 'team_id', 'score_b': 'official_points'}),
])

check = official.merge(rebuilt, on=['game_id', 'team_id'], how='inner')
check = check[check['game_id'].isin(pbp['game_id'].unique())]
exact = (check['rebuilt_points'] == check['official_points']).mean() * 100
print(f"\nTeam-games where play-by-play points match the final score: {exact:.1f}% (of {len(check):,})")

# 3.2 Possessions per game: should be close to the ~72 estimated from the box score
poss_per_team_game = len(possessions) / (pbp['game_id'].nunique() * 2)
print(f"Possessions per team per game: {poss_per_team_game:.1f}")

# 3.3 Share of possessions we could not classify
print("\n=== Possessions by origin ===")
origin_counts = possessions['origin'].value_counts()
print(origin_counts)
print(f"Unknown share: {origin_counts.get('unknown', 0) / len(possessions) * 100:.1f}%")

# ============================================================
# 4. POINTS PER POSSESSION, BY HOW THE TEAM GOT THE BALL
# A possession starts when a team gets the ball by a defensive rebound, a steal, a turnover
# or an opponent basket. Offensive rebounds do not start a new possession: the points scored
# after them are second chance points of the possession that missed.
# ============================================================

classified = possessions[~possessions['origin'].isin(['unknown', 'off_rebound'])]

order = ['after_score', 'def_rebound', 'turnover_other', 'steal']

summary = classified.groupby('origin').agg(
    n=('possession_points', 'size'),
    ppp=('possession_points', 'mean'),
    first_attack=('points', 'mean'),
    second_chance=('second_chance_points', 'mean'),
    std=('possession_points', 'std'),
).loc[order]
summary['se'] = summary['std'] / np.sqrt(summary['n'])
summary['ci95'] = 1.96 * summary['se']

print("\n=== Points per possession, by how the ball was won ===")
print(summary[['n', 'ppp', 'first_attack', 'second_chance', 'ci95']].round(3))

# ============================================================
# 5. EXTRA POINTS VS A DEFENSIVE-REBOUND POSSESSION
# A team that misses and loses the rebound, or turns the ball over, scores nothing on that possession.
# What differs is what the other team gets next. We take the possession that follows a defensive
# rebound as the reference and measure how many extra points each other way of getting the ball gives.
# ============================================================

ref_mean, ref_se = summary.loc['def_rebound', 'ppp'], summary.loc['def_rebound', 'se']

premium = summary.drop('def_rebound').copy()
premium['premium'] = premium['ppp'] - ref_mean
premium['ci95_premium'] = 1.96 * np.sqrt(premium['se'] ** 2 + ref_se ** 2)

print("\n=== Extra points vs a defensive-rebound possession ===")
print(premium[['premium', 'ci95_premium']].round(3))

by_season = classified.groupby(['season_code', 'origin'])['possession_points'].mean().unstack()
by_season_premium = by_season.sub(by_season['def_rebound'], axis=0)[['steal', 'turnover_other', 'after_score']]
print("\n=== Extra points vs a defensive-rebound possession, by season ===")
print(by_season_premium.round(3))

# ============================================================
# 6. VALUE OF AN OFFENSIVE REBOUND
# An offensive rebound is not a new possession but an extra attack inside the same one:
# we measure the points scored from the rebound until the team loses the ball.
# ============================================================

root_origin = segments['root'].map(segments['origin'])
oreb = segments[(segments['origin'] == 'off_rebound') & (~root_origin.isin(['unknown', 'off_rebound'])) & (segments['actions'] > 0)]
print("\n=== Points scored after an offensive rebound, until the ball is lost ===")
print(f"n = {len(oreb):,} | mean = {oreb['points'].mean():.3f} | 95% CI = ±{1.96 * oreb['points'].std() / np.sqrt(len(oreb)):.3f}")
print(f"Reference, average possession: {classified['possession_points'].mean():.3f}")

# ============================================================
# 7. PLOT 1: POINTS PER POSSESSION, FIRST ATTACK + SECOND CHANCES
# ============================================================

labels = {
    'after_score': 'After an opponent\nbasket',
    'def_rebound': 'After a defensive\nrebound',
    'turnover_other': 'After a turnover\n(no steal)',
    'steal': 'After a steal',
}

fig_origin, ax = plt.subplots(figsize=(8.5, 5))
fig_origin.patch.set_facecolor(BG)
ax.set_facecolor(BG)

x = np.arange(len(summary))
ax.bar(x, summary['first_attack'], color=GOLD, width=0.6, label='First attack')
ax.bar(x, summary['second_chance'], bottom=summary['first_attack'], color=GREEN, width=0.6,
       label='Second chances (after offensive rebounds)')
ax.errorbar(x, summary['ppp'], yerr=summary['ci95'], fmt='none', ecolor=TEXT, capsize=4)

ax.set_xticks(x)
ax.set_xticklabels([labels[o] for o in summary.index], color=TEXT)
ax.set_ylabel('Points per possession', color=TEXT)
ax.set_title('Points per possession, by how the ball was won', color=TEXT)
ax.set_ylim(0, summary['ppp'].max() * 1.25)
ax.tick_params(colors=TEXT)
for spine in ['top', 'right']:
    ax.spines[spine].set_visible(False)
for xi, val in zip(x, summary['ppp']):
    ax.text(xi, val + 0.04, f'{val:.2f}', ha='center', fontweight='bold', color=TEXT)
legend = ax.legend(loc='upper left', facecolor=BG, edgecolor=TEXT)
plt.setp(legend.get_texts(), color=TEXT)

plt.tight_layout()
fig_origin.savefig(os.path.join(IMAGES_DIR, 'points_by_possession_origin.png'), dpi=150, facecolor=fig_origin.get_facecolor())
plt.show()

# ============================================================
# 8. PLOT 2: EXTRA POINTS VS A DEFENSIVE-REBOUND POSSESSION
# ============================================================

premium_plot = premium.loc[['steal', 'after_score', 'turnover_other']]

fig_swing, ax = plt.subplots(figsize=(8, 3.8))
fig_swing.patch.set_facecolor(BG)
ax.set_facecolor(BG)

ypos = np.arange(len(premium_plot))[::-1]
bar_colors = [GREEN if v > 0 else CORAL for v in premium_plot['premium']]
ax.barh(ypos, premium_plot['premium'], xerr=premium_plot['ci95_premium'], color=bar_colors,
        height=0.55, capsize=4, ecolor=TEXT)
ax.set_yticks(ypos)
ax.set_yticklabels([labels[o].replace('\n', ' ') for o in premium_plot.index], color=TEXT)
ax.axvline(0, color=TEXT, linewidth=0.8)
ax.set_xlabel('Extra points per possession vs a possession after a defensive rebound (95% CI)', color=TEXT)
ax.set_title('Which ways of getting the ball are worth more?', color=TEXT)
ax.tick_params(colors=TEXT)
for spine in ['top', 'right']:
    ax.spines[spine].set_visible(False)

plt.tight_layout()
fig_swing.savefig(os.path.join(IMAGES_DIR, 'premium_vs_defensive_rebound.png'), dpi=150, facecolor=fig_swing.get_facecolor())
plt.show()
