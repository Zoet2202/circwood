import pandas as pd

path = './Volume_dataset/data/' 
volume_df = pd.read_csv(f'{path}sankey_data.csv')
print(volume_df.head())

keys = []

carbon_df = volume_df.copy()
carbon_df['carbon'] = 0
carbon_df['species'] = None
carbon_df['state'] = None

mapping_species_state = {

    'Spruce': {
        'species': 'spruce',
        'state': 'raw'
    },
    'Pine': {
        'species': 'pine',
        'state': 'raw'
    },
    'Non-coniferous': {
        'species': 'Non-coniferous',
        'state': 'raw'
    },
    'Import Firewood': {
        'species': '',
        'state': ''
    },
    'Bark': {
        'species': '',
        'state': 'residue'
    },
    'Residues from harvest': {
        'species': '',
        'state': 'residue'
    },
    'Mortality': {
        'species': '',
        'state': 'dead'
    },
    'Firewood': {
        'species': '',
        'state': ''
    },
    'Sawmills': {
        'species': '',
        'state': ''
    },
    'Shrinkage': {
        'species': '',
        'state': 'loss'
    },
    'Export Sawn': {
        'species': '',
        'state': ''
    },
    'Sawmills_stock': {
        'species': '',
        'state': ''
    },
    'Planingmills': {
        'species': 'spruce',
        'state': 'dry'
    },
    'Planingmills_stock': {
        'species': '',
        'state': ''
    },
    'Import Boards': {
        'species': '',
        'state': ''
    },
    'Boardmills': {
        'species': '',
        'state': ''
    },
    'Pulpmills': {
        'species': '',
        'state': ''
    },
    'Export Planed': {
        'species': '',
        'state': ''
    },
    'Impregnation': {
        'species': '',
        'state': ''
    },
    'Export Impregnated': {
        'species': '',
        'state': ''
    },
    'Boardmills_stock': {
        'species': '',
        'state': ''
    },
    'Pulpmills_': {
        'species': '',
        'state': ''
    },
    'Loss Pulpmills': {
        'species': '',
        'state': ''
    },
    'Loss Boardmills': {
        'species': '',
        'state': ''
    },
    'Wood Product  & Logistics': {
        'species': '',
        'state': ''
    },
    'Long-term USE': {
        'species': '',
        'state': ''
    }
}

#carbon_df['source_clean'] = carbon_df['source'].str.strip().str.title()

carbon_df['species'] = carbon_df['source'].apply(lambda x: mapping_species_state.get(x, {}).get('species', 'Unknown'))
carbon_df['state'] = carbon_df['source'].apply(lambda x: mapping_species_state.get(x, {}).get('state', 'Unknown'))

carbon_mapping = {
    ('spruce', 'raw'): 190,
    ('spruce', 'dry'): 203,
    ('pine', 'raw'): 215,
    ('pine', 'dry'): 228,
    ('non-coniferous', 'raw'): 240,
    ('non-coniferous', 'dry'): 268

}

carbon_df['carbon_total'] = carbon_df.apply(
    lambda row: row['volume'] * carbon_mapping.get(
        (row['species'].lower(), row['state'].lower()), 
        0
    ), 
    axis=1
)

print(carbon_df[carbon_df['source']=='Planingmills'].head())

carbon_df.to_csv(f'{path}carbon_data.csv', index=False)

