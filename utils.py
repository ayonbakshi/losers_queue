import json
import os

id_to_champ_json = 'id_to_champ.json'
if not os.path.isfile(id_to_champ_json):
    import cassiopeia as cass
    CHAMP_ID_TO_NAME = {
        str(champion.id): champion.name for champion in cass.get_champions(region='NA')
    }
    with open(id_to_champ_json, 'w', encoding='utf-8') as f:
        json.dump(CHAMP_ID_TO_NAME, f)
else:
    with open(id_to_champ_json, 'r', encoding='utf-8') as f:
        CHAMP_ID_TO_NAME = json.load(f)


def kda_str(kills, deaths, assists):
    if deaths == 0:
        kda = f'{0:.2f}' if kills + assists == 0 else 'Perfect'
    else:
        kda = f'{(kills+assists)/deaths:.2f}'
    
    return kda

def get_leaderboard(elos_dict, names=None, key=None):
    if names is None:
        names = elos_dict.keys()
        
    # populate missing names with default value
    for name in names:
        elos_dict[name]

    # identity
    if not key:
        key = lambda x: x

    elos = sorted(elos_dict.items(), key=lambda x: key(x[1]), reverse=True)
    return elos