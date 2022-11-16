# try to pip install lcu_driver
import sys
import subprocess

subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'lcu-driver'])
from lcu_driver import Connector



import shutil
import os
import json

connector = Connector()

async def haha_funny(connection):

    victim = 'volatile int'

    friends = await connection.request('get', '/lol-chat/v1/friends')

    if friends.status != 200:
        print(f'getting friends failed with status {friends.status}')
        return
    

    print('Got friends successfully')
    f_list = await friends.json()
    for f in f_list:
        if f['name'] == victim:
            s_id = f['id']
            break
    else:
        print(f'cant find {victim} on friends list')
        return
    
    print(s_id)

def save_matches(match_jsons, archive=True):
    if os.path.isdir('curr_matches') and len(os.listdir('curr_matches')) > 0:
        print('please remove "curr_matches" directory and re-run')
        return
    
    if not os.path.exists('curr_matches'):
        os.makedirs('curr_matches')

    for match_json in match_jsons:
        match_id = match_json['gameId']
        with open(os.path.join('curr_matches', f'lolmatch_{match_id}.json'), 'w', encoding='utf-8') as f:
            json.dump(match_json, f, indent=4)
        print(f'saved match {match_id}')

    if archive:
        shutil.make_archive('curr_matches', 'zip', 'curr_matches')
    

async def get_customs(connection):
    matches_r = await connection.request('get', '/lol-match-history/v1/products/lol/current-summoner/matches')
    if matches_r.status != 200:
        print(f'getting match history failed with status {matches_r.status}')
        return
    
    matches_d = (await matches_r.json())['games']
    if matches_d['gameCount'] != len(matches_d['games']):
        print('gameCount inconsistent with length of matches list')

    matches = matches_d['games']
    customs = []
    for match in matches:
        match_id = match['gameId']
        game_type = match['gameType']
        print(f'Found match {match_id} with type {game_type}')
        if game_type == 'CUSTOM_GAME':
            customs.append(match_id)

    match_jsons = []
    for match_id in customs:
        match_r = await connection.request('get', f'/lol-match-history/v1/games/{match_id}')
        if match_r.status != 200:
            print(f'failed to get match {match_id} with status {match_r.status}')
            continue

        match_json = await match_r.json()
        match_jsons.append(match_json)

    save_matches(match_jsons, archive=True)
    


# fired when LCU API is ready to be used
@connector.ready
async def connect(connection):
    print('LCU API is ready to be used.')

    # check if the user is already logged into his account
    summoner = await connection.request('get', '/lol-summoner/v1/current-summoner')
    if summoner.status != 200:
        print('Please login into your account')
    else:
        await get_customs(connection)


# fired when League Client is closed (or disconnected from websocket)
@connector.close
async def disconnect(_):
    print('Done')

# starts the connector
connector.start()