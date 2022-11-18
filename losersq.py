import json
from collections import defaultdict
import copy
import os
from attr import dataclass
from datetime import datetime
import pytz
from typing import Any, Optional

from utils import CHAMP_ID_TO_NAME, kda_str, get_leaderboard

import trueskill

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

    def _team_stats_str(self, name, t: Team, team_rating_before, team_rating_after):
        return '--'.join([
            f'{name}',
            f'(Rating {team_rating_before} -> {team_rating_after})',
            f'({t.tower_kills} T)',
            f'({t.inhibitor_kills} I)',
            f'({t.dragon_kills} D)',
            f'({t.rift_kills} RH)',
            f'({t.baron_kills} B)',
            f'(Banned {" ".join(t.bans)})',
        ])
            

    def _participant_stats_str(self, name, p: Participant, rating_before, rating_after, rank_before, rank_after):
        kda = kda_str(p.kills, p.deaths, p.assists)

        return ''.join([
            f'{name}'.ljust(15),
            f'{p.champ} [{p.level}]'.ljust(22),
            f'{p.kills}/{p.deaths}/{p.assists} ({kda})'.ljust(20),
            f'Damage {p.dmg_to_champs}'.ljust(14),
            f'CS {p.cs} ({p.cs/(self.match_duration/60):.1f}/m)'.ljust(16),
            f'Rating {rating_before} [{rank_before:02}] -> {rating_after} [{rank_after:02}]',
        ])

    def _match_stats_str(self):
        m, s = divmod(self.match_duration, 60)
        creation = datetime.fromtimestamp(self.creation_time, tz=pytz.timezone('US/Eastern'))
        return '=='.join([
            f'{creation.date()}-{creation.time().strftime("%H:%M:%S")}',
            f"{m}m{s:02}s"
        ])
    
    def as_str(self, ratings_before, ratings_after, team_rating_f) -> str:
        leaderboard_before = get_leaderboard(ratings_before, names=ratings_after.keys())
        leaderboard_after = get_leaderboard(ratings_after)
        ranks_before = {name : i for i, (name, _) in enumerate(leaderboard_before, 1)}
        ranks_after = {name : i for i, (name, _) in enumerate(leaderboard_after, 1)}

        winner_strs, loser_strs = [], []
        for name, p in self.participants.items():
            team = winner_strs if p.win else loser_strs
            team.append(self._participant_stats_str(
                name, p,
                ratings_before[name], ratings_after[name],
                ranks_before[name], ranks_after[name]))

        w_team_str = self._team_stats_str(
            'WINNERS', self.winning_team,
            team_rating_f([ratings_before[name] for name in self.winning_team.team_members]),
            team_rating_f([ratings_after[name] for name in self.winning_team.team_members]))

        l_team_str = self._team_stats_str(
            'LOSERS', self.losing_team,
            team_rating_f([ratings_before[name] for name in self.losing_team.team_members]),
            team_rating_f([ratings_after[name] for name in self.losing_team.team_members]))

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

class RatingSystem:
    @dataclass
    class Player:
        name: str
        champ: Optional[str] = None

    def get_default(self):
        raise NotImplementedError
    
    # for sorting leaderboard
    def rating_key(self, rating):
        return rating

    # for aggragating ratings into a singular rating
    def team_rating(self, ratings):
        return sum(ratings) / len(ratings)

    def get_new_ratings(self, winners: list[Player], losers: list[Player], ratings: dict[str, Any]):
        raise NotImplementedError

class EloSystem(RatingSystem):

    class Elo(float):
        def __new__(self, value):
            return float.__new__(self, value)

        def __init__(self, value):
            float.__init__(value)

        def __str__(self):
            return f'{round(self)}'
    

    def __init__(self, start_elo=1500, k=32):
        self._start_elo = start_elo
        self._k = k

    def get_default(self):
        return EloSystem.Elo(self._start_elo)

    def team_rating(self, ratings):
        return EloSystem.Elo(super().team_rating(ratings))

    def get_new_ratings(self,
                        winners: list[RatingSystem.Player],
                        losers: list[RatingSystem.Player],
                        ratings: dict[str, float]) -> dict[str, float]:

        new_ratings = copy.deepcopy(ratings)
        
        R_winner = sum(ratings[player.name] for player in winners) / len(winners)
        R_loser = sum(ratings[player.name] for player in losers) / len(losers)
        R_delta = R_loser - R_winner
        winner_delta = 1 - 1 / (1 + 10**(R_delta/400))
        
        for player in winners:
            new_elo = EloSystem.Elo(new_ratings[player.name] + self._k * winner_delta)
            new_ratings[player.name] = new_elo
        for player in losers:
            new_elo = EloSystem.Elo(new_ratings[player.name] - self._k * winner_delta)
            new_ratings[player.name] = new_elo
        
        return new_ratings

class TrueSkill(RatingSystem):

    class Rating(trueskill.Rating):

        def __init__(self, mu=None, sigma=None):
            super().__init__(mu, sigma)

        def __str__(self):
            return f'{round(self.mu):04}±{round(self.sigma):04}'
    

    def __init__(self, mu=1500, sigma=500, beta=None, tau=None):
        if beta is None:
            beta = sigma / 2
        if tau is None:
            tau = sigma / 100
        
        trueskill.setup(mu=mu, sigma=sigma, beta=beta, tau=tau, draw_probability=0)


    def get_default(self):
        return TrueSkill.Rating()

    def rating_key(self, rating: 'TrueSkill.Rating'):
        return (rating.mu, -rating.sigma)

    def team_rating(self, ratings):
        total_mu = sum(r.mu for r in ratings) / len(ratings)
        total_sigma = sum(r.sigma for r in ratings) / len(ratings)
        return TrueSkill.Rating(mu=total_mu, sigma=total_sigma)

    def get_new_ratings(self,
                        winners: list[RatingSystem.Player],
                        losers: list[RatingSystem.Player],
                        ratings: dict[str, 'TrueSkill.Rating']) -> dict[str, 'TrueSkill.Rating']:

        R_winners = {p.name: ratings[p.name] for p in winners}
        R_losers = {p.name: ratings[p.name] for p in losers}

        updated_ratings = \
            trueskill.rate([R_winners, R_losers], ranks=[0, 1])

        new_ratings = copy.deepcopy(ratings)
        for team in updated_ratings:
            for name, rating in team.items():
                new_ratings[name] = TrueSkill.Rating(rating.mu, rating.sigma)

        return new_ratings

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

    def get_leaderboard_str(self, rating, champ=None):
        win, loss = self.get_win_loss(champ=champ)
        
        k, d, a = self.get_avg_kda(champ=champ)
        kda = kda_str(k, d, a)

        doubles, triples, quadras, pentas = self.get_multikills(champ=champ)

        return ' | '.join([
            f'{self.name}'.ljust(15),
            f'Rating {rating}'.ljust(10),
            f'W/L {win}/{loss} [{win+loss}]'.ljust(15),
            f'KDA {k:.1f}/{d:.1f}/{a:.1f} ({kda})'.ljust(25),
            f'(D: {doubles}, T: {triples}, Q: {quadras}, P: {pentas})'.ljust(15)
        ])



class LosersQueue:

    def __init__(self, match_files, alias_file=None):

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

        self._rating_system = TrueSkill()
        start_ratings = defaultdict(self._rating_system.get_default)
        self._rating_history = self.calculate_rating_history(
            start_ratings=start_ratings, matches=self._matches, rating_system=self._rating_system)
        
        self._player_stats = {name : PlayerStats(name=name, matches=self._matches) for name in self._rating_history[0]}

    def calculate_rating_history(self, start_ratings, matches: list[Match], rating_system: RatingSystem):
        '''
        on return, rating_history[i] is rating after ith game
        note that this mean len(rating_history) == len(matches) + 1
        '''
        rating_history = [start_ratings]
        for match in reversed(matches):
            winners, losers = [], []
            for name, p in match.participants.items():
                team = winners if p.win else losers
                team.append(RatingSystem.Player(name=name, champ=p.champ))

            new_ratings = self._rating_system.get_new_ratings(winners=winners, losers=losers, ratings=rating_history[-1])
            rating_history.append(new_ratings)

        rating_history.reverse()
        return rating_history

    def print_stats(self, name, champ=None):
        ratings = self._rating_history[0]
        stat_str = self._player_stats[name].get_leaderboard_str(ratings[name], champ=champ)

        print('\n'.join([
            '=' * len(stat_str),
            stat_str,
            '=' * len(stat_str),
        ]))

    def leaderboard(self):
        ratings = get_leaderboard(self._rating_history[0])

        stat_strs = []
        for name, rating in ratings:
            stats = self._player_stats[name]
            stat_strs.append(stats.get_leaderboard_str(rating))

        width = max(len(stat_str) for stat_str in stat_strs)
        print('\n'.join([
            '=' * width,
            '\n'.join(stat_strs),
            '=' * width,
        ]))

    
    def print_matches(self, name=None, champ=None, n=3):
        matches = self._player_stats[name]._matches_with_champ(champ) if name is not None else self._matches
        for match, (ratings_before, ratings_after) \
            in zip(matches[:n], zip(self._rating_history[1:], self._rating_history)):
            print(match.as_str(ratings_before, ratings_after, self._rating_system.team_rating))
    


if __name__ == '__main__':
    root_dir = 'matches'
    match_files = [os.path.join(root_dir, file) for file in os.listdir(root_dir)]
    lq = LosersQueue(match_files=match_files)
    # lq.leaderboard()
    # lq.print_matches()