import numpy as np
import pandas as pd

# ============================================================
# Shared logic: split every game into possessions using the play-by-play log.
#
# A "segment" starts the moment a team gets the ball and ends when the other team gets it.
# Each segment is labelled by HOW the team got the ball:
#   after_score     : inbound after an opponent basket or last made free throw
#   def_rebound     : defensive rebound
#   off_rebound     : offensive rebound (the segment starts at the rebound)
#   steal           : turnover with a steal
#   turnover_other  : turnover without a steal (out of bounds, travel, offensive foul, ...)
#   unknown         : start of a period, technical fouls, or any case we cannot classify
#
# An offensive rebound opens a new segment, but it belongs to the same possession as the
# shot that was missed. The column "root" links every segment to the first segment of its
# possession, so that second chance points are credited to the possession that created them.
# ============================================================

POINTS = {'2FGM': 2, '3FGM': 3, 'LAYUPMD': 2, 'DUNK': 2}   # made field goals
MISSES = {'2FGA', '3FGA', 'LAYUPATT', '2FGAB', '3FGAB'}     # missed field goals (AB = blocked)
# Events that can sit between two free throws of the same trip without ending it
SKIP_BETWEEN_FT = {'IN', 'OUT', 'AS', 'RV', 'CM', 'TOUT', 'TOUT_TV', 'CCH', 'FV', 'AG'}
# Technical, unsportsmanlike, coach, bench fights: free throws after these do not follow the normal rules
SPECIAL_FOULS = {'CMT', 'CMU', 'CMD', 'CMTI', 'C', 'B', 'F', 'BF'}
PERIOD_BOUNDARY = {'BP', 'EP', 'EG'}


def clock_to_seconds(marker_time):
    """'MM:SS' (time left in the period) to seconds. Missing values become -1."""
    parts = marker_time.str.split(':', expand=True)
    minutes = pd.to_numeric(parts[0], errors='coerce')
    seconds = pd.to_numeric(parts[1], errors='coerce')
    return (minutes * 60 + seconds).fillna(-1).astype(int).values


def build_segments(pbp, header):
    """
    pbp must be sorted by game_id and number_of_play and contain:
    game_id, season_code, quarter, team_id, play_type, marker_time.
    Returns one row per segment.
    """
    game_ids = pbp['game_id'].values
    seasons = pbp['season_code'].values
    teams = pbp['team_id'].values
    plays = pbp['play_type'].values
    quarters = pbp['quarter'].values
    clock = clock_to_seconds(pbp['marker_time'])
    n_events = len(pbp)

    # Opponent lookup from the header (team_id_a <-> team_id_b)
    opp_lookup = {}
    for row in header[['game_id', 'team_id_a', 'team_id_b']].itertuples(index=False):
        opp_lookup[(row.game_id, row.team_id_a)] = row.team_id_b
        opp_lookup[(row.game_id, row.team_id_b)] = row.team_id_a

    seg_game, seg_season, seg_team, seg_origin = [], [], [], []
    seg_pts, seg_actions, seg_root = [], [], []
    seg_quarter, seg_start_clock, seg_first_action_clock = [], [], []
    seg_first_action_play = []

    def new_segment(i, team, origin, root=None):
        seg_game.append(game_ids[i])
        seg_season.append(seasons[i])
        seg_team.append(team)
        seg_origin.append(origin)
        seg_pts.append(0)
        seg_actions.append(0)
        seg_quarter.append(quarters[i])
        seg_start_clock.append(clock[i])
        seg_first_action_clock.append(-1)
        seg_first_action_play.append('')
        idx = len(seg_game) - 1
        seg_root.append(idx if root is None else root)
        return idx

    def next_relevant_event(i):
        """Index of the next event after i that is not a substitution, assist, foul-drawn, timeout, etc."""
        j = i + 1
        while j < n_events and game_ids[j] == game_ids[i] and plays[j] in SKIP_BETWEEN_FT:
            j += 1
        return j

    state = {'idx': -1, 'team': None, 'made_by': None, 'made_idx': -1, 'special': False}

    def act(i, team):
        """A team takes an action (shot, free throw, turnover). Make sure it holds a segment."""
        if state['idx'] == -1 or state['team'] != team:
            state['idx'] = new_segment(i, team, 'unknown')
            state['team'] = team
        idx = state['idx']
        if seg_actions[idx] == 0:
            seg_first_action_clock[idx] = clock[i]
            seg_first_action_play[idx] = plays[i]
        seg_actions[idx] += 1

    current_game = None

    for i in range(n_events):
        game = game_ids[i]
        if game != current_game:
            current_game = game
            state.update(idx=-1, team=None, made_by=None, made_idx=-1, special=False)

        play = plays[i]
        team = teams[i]

        if play in PERIOD_BOUNDARY:
            state.update(idx=-1, team=None, made_by=None, special=False)
            continue

        if play in SPECIAL_FOULS:
            state['special'] = True
            if state['idx'] != -1:
                seg_origin[state['idx']] = 'unknown'
            continue

        if play in POINTS:
            state.update(made_by=None, special=False)
            act(i, team)
            seg_pts[state['idx']] += POINTS[play]
            scorer_idx = state['idx']
            opp = opp_lookup.get((game, team))
            if opp is not None:
                state['idx'] = new_segment(i, opp, 'after_score')
                state['team'] = opp
            state['made_by'], state['made_idx'] = team, scorer_idx
            continue

        if play in MISSES:
            state.update(made_by=None, special=False)
            act(i, team)
            continue

        if play == 'O':
            state.update(made_by=None, special=False)
            # Same possession as the missed shot if the same team still holds the ball
            root = seg_root[state['idx']] if (state['idx'] != -1 and state['team'] == team) else None
            state['idx'] = new_segment(i, team, 'off_rebound', root=root)
            state['team'] = team
            continue

        if play == 'D':
            state.update(made_by=None, special=False)
            state['idx'] = new_segment(i, team, 'def_rebound')
            state['team'] = team
            continue

        if play == 'TO':
            state.update(made_by=None, special=False)
            act(i, team)
            opp = opp_lookup.get((game, team))
            if opp is not None:
                # In the log the steal comes right after the turnover
                has_steal = False
                for j in range(i + 1, min(n_events, i + 4)):
                    if game_ids[j] != game or plays[j] in PERIOD_BOUNDARY:
                        break
                    if plays[j] == 'ST' and teams[j] == opp:
                        has_steal = True
                        break
                state['idx'] = new_segment(i, opp, 'steal' if has_steal else 'turnover_other')
                state['team'] = opp
            continue

        if play in ('FTM', 'FTA'):
            # And-one: the shooter's team already lost control in our state, points go to its own segment
            if state['made_by'] == team and state['team'] != team:
                target_idx = state['made_idx']
            else:
                act(i, team)
                target_idx = state['idx']

            if play == 'FTM':
                seg_pts[target_idx] += 1

            # Is this the last free throw of the trip?
            j = next_relevant_event(i)
            more_ft = (
                j < n_events and game_ids[j] == game and plays[j] in ('FTM', 'FTA')
                and teams[j] == team and quarters[j] == quarters[i]
            )
            if play == 'FTM' and not more_ft and not state['special']:
                opp = opp_lookup.get((game, team))
                if opp is not None:
                    state['idx'] = new_segment(i, opp, 'after_score')
                    state['team'] = opp
                state['made_by'] = None
            continue

    segments = pd.DataFrame({
        'game_id': seg_game, 'season_code': seg_season, 'team_id': seg_team,
        'origin': seg_origin, 'points': seg_pts, 'actions': seg_actions,
        'root': seg_root, 'quarter': seg_quarter,
        'start_clock': seg_start_clock, 'first_action_clock': seg_first_action_clock,
        'first_action_play': seg_first_action_play
    })

    # Seconds from getting the ball to the first shot, free throw or turnover.
    # Only valid when both clock readings exist and the clock is running down.
    elapsed = segments['start_clock'] - segments['first_action_clock']
    valid = (segments['start_clock'] >= 0) & (segments['first_action_clock'] >= 0) & (elapsed >= 0)
    segments['seconds_to_first_action'] = np.where(valid, elapsed, np.nan)

    return segments


def possession_table(segments):
    """
    One row per possession: the root segment, with the points of the whole chain
    (the first attack plus any second chances after offensive rebounds).
    """
    chain_points = segments.groupby('root')['points'].sum().rename('possession_points')
    roots = segments[segments.index == segments['root']].copy()
    roots = roots.join(chain_points, how='left')
    roots['second_chance_points'] = roots['possession_points'] - roots['points']
    return roots[roots['actions'] > 0]
