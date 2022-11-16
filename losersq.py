import json
from collections import defaultdict
import copy
import os
from attr import dataclass
from datetime import timedelta, datetime
import pytz
from typing import Optional

from utils import CHAMP_ID_TO_NAME, kda_str, get_leaderboard

class Participant:
    
    def __init__(self, participant_dict):
        self.team_id = participant_dict['teamId']
        self.champ = CHAMP_ID_TO_NAME[str(participant_dict['championId'])]

        self._stats = participant_dict['stats']

        self.win = self._stats['win']
        self.kills = self._stats['kills']
        self.deaths = self._stats['deaths']
        self.assists = self._stats['assists']
        self.multikills = tuple(self._stats[multikill] for multikill in \
            ('doubleKills', 'tripleKills', 'quadraKills', 'pentaKills'))

        self.level = self._stats['champLevel']
        self.dmg_to_champs = self._stats['totalDamageDealtToChampions']
        self.cs = self._stats['totalMinionsKilled'] + self._stats['neutralMinionsKilled']

class Team:

    def __init__(self, team_dict, participants):
        self.team_id = team_dict['teamId']
        self.team_members = [name for name, p in participants.items() if p.team_id == self.team_id]

        self.win = team_dict['win'] == 'Win'

        self.bans = [CHAMP_ID_TO_NAME[str(b['championId'])] for b in team_dict['bans']]

        self.dragon_kills = team_dict['dragonKills']
        self.rift_kills = team_dict['riftHeraldKills']
        self.baron_kills = team_dict['baronKills']
        self.tower_kills = team_dict['towerKills']
        self.inhibitor_kills = team_dict['inhibitorKills']




class Match:
    
    def __init__(self, match_file):
        with open(match_file, 'r', encoding='utf-8') as f:
            match_dict = json.load(f)

        self.id = match_dict['gameId']
        self.match_duration = match_dict['gameDuration']

        # time since epoch (seconds)
        self.creation_time = match_dict['gameCreation'] / 1000

        pids = {}
        for participant_info in match_dict['participantIdentities']:
            pid = participant_info['participantId']
            name = participant_info['player']['summonerName']
            pids[pid] = name

        self.participants: dict[str, Participant] = {}
        for participant_dict in match_dict['participants']:
            pid = participant_dict['participantId']
            name = pids[pid]
            self.participants[name] = \
                Participant(participant_dict=participant_dict)
        
        teams = [Team(team_dict, self.participants) for team_dict in match_dict['teams']]
        teams.sort(key=lambda t: t.win)
        self.losing_team, self.winning_team = teams

        self._elos_before, self._elos_after = None, None
        self._ranks_before, self._ranks_after = None, None

    def set_elos(self, elos_before, elos_after):
        self._elos_before = elos_before
        self._elos_after = elos_after

        leaderboard_before = get_leaderboard(self._elos_before, names=self._elos_after.keys())
        leaderboard_after = get_leaderboard(self._elos_after)
        self._ranks_before = {name : i for i, (name, _) in enumerate(leaderboard_before, 1)}
        self._ranks_after = {name : i for i, (name, _) in enumerate(leaderboard_after, 1)}


    def _team_stats_str(self, name, t: Team):
        avg_elo_before = sum(self._elos_before[name] for name in t.team_members) / len(t.team_members)
        avg_elo_after = sum(self._elos_after[name] for name in t.team_members) / len(t.team_members)

        return '--'.join([
            f'{name}',
            f'(Rating {round(avg_elo_before)} -> {round(avg_elo_after)})',
            f'({t.tower_kills} T)',
            f'({t.inhibitor_kills} I)',
            f'({t.dragon_kills} D)',
            f'({t.rift_kills} RH)',
            f'({t.baron_kills} B)',
            f'(Banned {" ".join(t.bans)})',
        ])
            

    def _participant_stats_str(self, name, p: Participant):
        kda = kda_str(p.kills, p.deaths, p.assists)
        elo_before, elo_after = self._elos_before[name], self._elos_after[name]
        rank_before, rank_after = self._ranks_before[name], self._ranks_after[name]

        return ''.join([
            f'{name}'.ljust(15),
            f'{p.champ} [{p.level}]'.ljust(22),
            f'{p.kills}/{p.deaths}/{p.assists} ({kda})'.ljust(20),
            f'Damage {p.dmg_to_champs}'.ljust(14),
            f'CS {p.cs} ({p.cs/(self.match_duration/60):.1f}/m)'.ljust(16),
            f'Rating {round(elo_before)} [{rank_before:02}] -> {round(elo_after)} [{rank_after:02}]',
        ])

    def _match_stats_str(self):
        m, s = divmod(self.match_duration, 60)
        creation = datetime.fromtimestamp(self.creation_time, tz=pytz.timezone('US/Eastern'))
        return '=='.join([
            f'{creation.date()}-{creation.time().strftime("%H:%M:%S")}',
            f"{m}m{s:02}s"
        ])


    def __str__(self):
        if self._elos_before is None and self._elos_after is None:
            self.set_elos(defaultdict(lambda: 'N/A'), defaultdict(lambda: 'N/A'))

        winner_strs, loser_strs = [], []
        for name, p in self.participants.items():
            team = winner_strs if p.win else loser_strs
            team.append(self._participant_stats_str(name, p))

        w_team_str = self._team_stats_str('WINNERS', self.winning_team)
        l_team_str = self._team_stats_str('LOSERS', self.losing_team)

        match_str = self._match_stats_str()

        width = max(len(line) for line in winner_strs + loser_strs)
        return '\n'.join([
            '=' * width,
            w_team_str.ljust(width, '-'),
            '-' * width,
            '\n'.join(winner_strs),
            '-' * width,
            '\n'.join(loser_strs),
            '-' * width,
            l_team_str.ljust(width, '-'),
            match_str.rjust(width, '=')
        ])

class EloSystem:

    @dataclass
    class Player:
        name: str
        elo: int
        champ: Optional[str] = None

    def __init__(self, k=32):
        self._k = k

    def update_elos(self, winners, losers) -> list[Player]:
        winners = copy.deepcopy(winners)
        losers = copy.deepcopy(losers)

        R_winner = sum(player.elo for player in winners) / len(winners)
        R_loser = sum(player.elo for player in losers) / len(losers)
        R_delta = R_loser - R_winner
        winner_delta = 1 - 1 / (1 + 10**(R_delta/400))

        
        
        for player in winners:
            player.elo += self._k * winner_delta
        for player in losers:
            player.elo -= self._k * winner_delta
        
        return winners + losers

class PlayerStats:

    def __init__(self, name, matches: list[Match]):
        self.name = name
        self._matches = [match for match in matches if name in match.participants]

    def _matches_with_champ(self, champ=None):
        if champ is None or champ == 'all':
            return self._matches
        return [match for match in self._matches if match.participants[self.name].champ == champ]

    def get_win_loss(self, champ=None):
        wins = losses = 0
        for match in self._matches_with_champ(champ):
            if match.participants[self.name].win:
                wins += 1
            else:
                losses += 1
        return wins, losses
    
    def get_avg_kda(self, champ=None):
        if not self._matches:
            return (0, 0, 0), 0

        kills = deaths = assists = 0
        n_matches = 0
        for match in self._matches_with_champ(champ):
            p = match.participants[self.name]
            kills += p.kills
            deaths += p.deaths
            assists += p.assists
            n_matches += 1

        avg_kda = tuple(stat / n_matches for stat in (kills, deaths, assists))
        return avg_kda

    def get_multikills(self, champ=None):
        multikills = (0, 0, 0, 0)
        for match in self._matches_with_champ(champ):
            p = match.participants[self.name]
            multikills = tuple(x + y for x,y in zip(multikills, p.multikills))
        return multikills

    def get_elo(self):
        return self._matches[0]._elos_after[self.name]

    def get_leaderboard_str(self, champ=None):
        win, loss = self.get_win_loss(champ=champ)
        
        k, d, a = self.get_avg_kda(champ=champ)
        kda = kda_str(k, d, a)

        doubles, triples, quadras, pentas = self.get_multikills(champ=champ)

        elo = self.get_elo()

        return ' | '.join([
            f'{self.name}'.ljust(15),
            f'Rating {round(elo)}'.ljust(10),
            f'W/L {win}/{loss} [{win+loss}]'.ljust(15),
            f'KDA {k:.1f}/{d:.1f}/{a:.1f} ({kda})'.ljust(25),
            f'(D: {doubles}, T: {triples}, Q: {quadras}, P: {pentas})'.ljust(15)
        ])



class LosersQueue:

    def __init__(self, match_files, start_elo=1500, alias_file=None):

        matches = {}

        for match_file in match_files:
            match = Match(match_file=match_file)
            t1, t2 = match.losing_team, match.winning_team
            if len(t1.team_members) == len(t2.team_members) == 5 and t1.win + t2.win == 1:
                matches[match.id] = match
            else:
                print(f'Discarding match {match.id}, {len(t1.team_members)=} {len(t2.team_members)=}, {t1.win=}, {t2.win=}')

        
        self._matches: list[Match] = list(matches.values())

        self._matches.sort(key=lambda m: m.creation_time, reverse=True)

        self._elo_system = EloSystem()
        start_elos = defaultdict(lambda: start_elo)
        self._elo_history = self.calculate_elo_history(
            start_elos=start_elos, matches=self._matches, elo_system=self._elo_system)
        
        self._player_stats = {name : PlayerStats(name=name, matches=self._matches) for name in self._elo_history[-1]}

    def calculate_elo_history(self, start_elos, matches: list[Match], elo_system: EloSystem):
        '''
        elo_history[i] is elo before ith game
        note that this mean len(elo_history) == len(matches) + 1
        '''
        elo_history = [start_elos]
        for match in reversed(matches):
            elos = copy.deepcopy(elo_history[-1])
            winners, losers = [], []
            for name, p in match.participants.items():
                team = winners if p.win else losers
                team.append(EloSystem.Player(name=name, elo=elos[name], champ=p.champ))

            for player in elo_system.update_elos(winners, losers):
                elos[player.name] = player.elo
            
            elo_history.append(elos)
            match.set_elos(elos_before=elo_history[-2], elos_after=elo_history[-1])

        return elo_history

    def print_stats(self, name, champ):
        stat_str = self._player_stats[name].get_leaderboard_str(champ=champ)

        print('\n'.join([
            '=' * len(stat_str),
            stat_str,
            '=' * len(stat_str),
        ]))

    def leaderboard(self):
        latest_elos = self._elo_history[-1]
        elos = get_leaderboard(latest_elos)

        stat_strs = []
        for name, _ in elos:
            stats = self._player_stats[name]
            stat_strs.append(stats.get_leaderboard_str())

        width = max(len(stat_str) for stat_str in stat_strs)
        print('\n'.join([
            '=' * width,
            '\n'.join(stat_strs),
            '=' * width,
        ]))

    
    def print_matches(self, name=None, champ=None, n=3):
        matches = self._player_stats[name]._matches_with_champ(champ) if name is not None else self._matches
        for match in matches[:n]:
            print(match)
    


if __name__ == '__main__':
    root_dir = 'matches'
    match_files = [os.path.join(root_dir, file) for file in os.listdir(root_dir)]
    lq = LosersQueue(match_files=match_files)
    # lq.leaderboard()
    # lq.print_matches()