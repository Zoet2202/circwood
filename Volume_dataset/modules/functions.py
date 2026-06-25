import os
import pandas as pd
import pyjstat
import json
from pyjstat import pyjstat
import requests


def process_query_files(directory_query_path):
    """
    Process JSON query files in a specified directory and return the results as a dictionary.

    Each JSON file in `directory_query_path` must contain the following structure:
        {
            "postUrl": <string URL>,
            "tableIdForQuery": <string table identifier>,
            "queryObj": <dict payload for POST request>
        }

    The function reads each `.json` file, sends a POST request to `postUrl` with `queryObj`,
    and then converts the JSON-stat response to a pyjstat object.

    Parameters:
    ----------
    directory_query_path : str
        The path to the directory containing the JSON query files.

    Returns:
    -------
    dict
        A dictionary where the keys are the table IDs (`tableIdForQuery`) and the values
        are lists of pyjstat.Dataset objects parsed from the JSON-stat response.

    Raises:
    ------
    Exception
        If any file fails to process (e.g., invalid JSON, request error, etc.), an error
        message is printed and the function continues with the next file.
    """

    dataset_output = {}

    for filename in os.listdir(directory_query_path):
        if filename.endswith('.json'):
            file_path = os.path.join(directory_query_path, filename)

            try:
                with open(file_path, 'r') as file:
                    data = json.load(file)

                POST_URL = data['postUrl']
                table = data['tableIdForQuery']
                payload = data['queryObj']

                result = requests.post(POST_URL, json=payload)
                result.raise_for_status()  # Raise an error for bad status codes

                # Convert JSON-stat to pyjstat object
                json_stat = json.loads(result.text)
                dataset = pyjstat.from_json_stat(json_stat)
                dataset_output[table] = dataset

                print(f"Processed file: {filename}, Table ID: {table}")

            except Exception as e:
                print(f"Error processing file {filename}: {e}")

    return dataset_output


def assign_source_trade(row, species):
    """
    Assign a 'source' string based on the species, product type, and net export for a given row.

    This function inspects `row['varenummer']` (the product code) for certain keywords
    (e.g., 'massevirke', 'skurtømmer') to map to an appropriate source.
    It then checks if `net_export` is positive or negative to differentiate
    between domestic and import flows.

    Parameters:
    ----------
    row : pandas.Series
        A single row from a DataFrame, expected to have 'varenummer' and 'net exports' columns.
    species : str
        The species classification (e.g. 'gran', 'furu', 'bjørk', etc.) to determine
        which mapping dictionary to use.

    Returns:
    -------
    str
        The assigned 'source' string based on the logic in the function, or 'None'
        if no mapping is found.
    """
    varenummer_lower = row['varenummer'].lower()
    net_export = row['net exports']

    if species in ['gran', 'furu']:
        product_map_source = {
            'massevirke': ('Spruce', 'Import Pulpmills'),
            'skurtømmer': ('Spruce', 'Import Sawmills'),
            'tømmer': ('Spruce', 'Import Boardmills'),
            'impregnert': ('Impregnation', 'Import Impregnated'),
            'skurlast': ('Planingmills', 'Import Sawnwood'),
            'høvellast': ('Wood Product processing & Logistics', 'Import Planed')
        }

    elif species in ['lauv', 'brensel', 'bjørk']:
        product_map_source = {
            'brensel': ('Non-coniferous', 'Import Firewood'),
            'lauv': ('Non-coniferous', 'Import Firewood'),
            'bjørk': ('Non-coniferous', 'Import Firewood')
        }
    elif species in ['bartre']:
        product_map_source = {
            'impregnert': ('Impregnation', 'Import Impregnated'),
            'skurtømmer': ('Spruce', 'Import Sawmills'),
            'uimpregnert': ('Sawmills', 'Import Planingmills'),
            'massevirke': ('Spruce', 'Import Pulpmills')
        }

    else:
        'None'

    for product, (pos_source, neg_source) in product_map_source.items():
        if product in varenummer_lower:
            if net_export > 0:
                return pos_source
            elif net_export < 0:
                return neg_source
    return 'None'


def assign_target_trade(row, species):
    """
    Assign a 'target' string based on the species, product type, and net export for a given row.

    Similar to `assign_source_trade`, this function checks the `varenummer` keyword
    and whether net exports are positive or negative. It then returns the appropriate
    'target' for that product flow.

    Parameters:
    ----------
    row : pandas.Series
        A single row from a DataFrame, expected to have 'varenummer' and 'net exports' columns.
    species : str
        The species classification (e.g. 'gran', 'furu', 'bjørk', etc.) to determine
        which mapping dictionary to use.

    Returns:
    -------
    str
        The assigned 'target' string based on the logic in the function, or 'None'
        if no mapping is found.
    """
    varenummer_lower = row['varenummer'].lower()
    net_export = row['net exports']

    if species in ['gran', 'furu']:
        product_map_target = {
            'massevirke': ('Export Pulpmills', 'Pulpmills'),
            'skurtømmer': ('Export Lumber', 'Sawmills'),
            'tømmer': ('Export Lumber', 'Boardmills'),
            'impregnert': ('Export Impregnated', 'Impregnation'),
            'skurlast': ('Export Sawnwood', 'Sawmills'),
            'høvellast': ('Export Planedwood', 'Planingmills')
        }
    elif species in ['lauv', 'bjørk', 'brensel']:
        product_map_target = {
            'brensel': ('Export Firewood', 'Import Firewood'),
            'lauv': ('Export Firewood', 'Import Firewood'),
            'bjørk': ('Export Firewood', 'Import Firewood')
        }
    elif species in ['bartre']:
        product_map_target = {
            'impregnert': ('Export Impregnated', 'Wood Product processing & Logistics'),
            'skurtømmer': ('Export Spruce', 'Sawmills'),
            'uimpregnert': ('Export Sawnwood', 'Planingmills'),
            'massevirke': ('Export Pulpwood', 'Pulpmills')
        }
    else:
        'None'

    for product, (pos_target, neg_target) in product_map_target.items():
        if product in varenummer_lower:
            if net_export > 0:
                return pos_target
            elif net_export < 0:
                return neg_target
    return 'None'


def get_source_target_treindustrien(row):
    """
    Determine the 'source' and 'target' for Treindustrien inventory data
    (e.g., timber inventory, planed timber inventory).

    This function uses `inventory_mapping` to look up the appropriate source-target
    pair based on the 'Category' of the row. If 'VolumeChange' is positive, it applies
    the 'positive' flow (e.g. 'Sawmills' -> 'Timber Inventory'), if negative, the
    'negative' flow.

    Parameters:
    ----------
    row : pandas.Series
        A row from `treindustrien_df` which contains:
            - 'Category'
            - 'VolumeChange'

    Returns:
    -------
    (str, str)
        A tuple of the form (source, target). If 'Category' is not in `inventory_mapping`,
        (None, None) is returned.
    """
    inventory_mapping = {
        'Timber inventory': {
            'positive': ('Sawmills', 'Timber Inventory'),
            'negative': ('Timber Inventory', 'Sawmills')
        },
        'sawn timber inventory': {
            'positive': ('Sawmills', 'Timber Inventory'),
            'negative': ('Timber Inventory', 'Sawmills')
        },
        'planed timber inventory': {
            'positive': ('Planingmills', 'planed timber inventory'),
            'negative': ('planed timber inventory', 'Planingmills')
        },
        'Impregnated inventory': {
            'positive': ('Impregnation', 'Impregnated inventory'),
            'negative': ('Impregnated inventory', 'Impregnation')
        }
    }
    cat = row['Category']
    change = row['VolumeChange']

    if cat in inventory_mapping:
        if change >= 0:
            return inventory_mapping[cat]['positive']
        else:
            return inventory_mapping[cat]['negative']
    return (None, None)


def boardmill_process(dataframe):
    """
    Split the Boardmills flows into two parts:
    1. A 'loss' portion (5% of the volume)
    2. A 'panels' portion (95% of the volume)

    Parameters
    ----------
    dataframe : pd.DataFrame
        A DataFrame with 'target', 'source', and 'volume' columns, among others.

    Returns
    -------
    pd.DataFrame
        The original DataFrame plus two new sets of rows:
        - Rows for 'Boardmills' loss (5% of volume)
        - Rows for 'Wood Product processing & Logistics' panels (95% of volume)
    """

    loss_factor = 0.05
    panels_factor = 1-loss_factor

    # Filter only rows where target == 'Boardmills'
    filtered_df = dataframe.loc[dataframe['target'] == 'Boardmills'].copy()

    # Assign 'source' to 'Boardmills' for these filtered rows
    filtered_df.loc[:, 'source'] = 'Boardmills'

    # Create a new DataFrame for the loss portion
    rows_loss = filtered_df.copy()
    rows_loss.loc[:, 'volume'] = rows_loss['volume'] * loss_factor
    rows_loss.loc[:, 'target'] = 'Loss Boardmills'

    # Create a new DataFrame for the panels portion
    rows_panels = filtered_df.copy()
    rows_panels.loc[:, 'volume'] = rows_panels['volume'] * panels_factor
    rows_panels.loc[:, 'target'] = 'Wood Product processing & Logistics'

    # Concatenate the original DataFrame with the two new sets of rows
    result_df = pd.concat(
        [dataframe, rows_loss, rows_panels], ignore_index=True)
    result_df.to_csv('boardmill_process.csv', index=False)
    return result_df


def pulpmills_process(dataframe, short_term_products, energy, loss):

    short_term_ratio = short_term_products/100
    energy_ratio = energy/100
    loss_ratio = loss/100

    filtered_df = dataframe[dataframe['target'] == 'Pulpmills'].copy()
    filtered_df.loc[:, 'source'] = 'Pulpmills'

    rows_short_term = filtered_df.copy()
    rows_short_term.loc[:,
                        'volume'] = rows_short_term['volume']*short_term_ratio
    rows_short_term.loc[:, 'target'] = 'Short-term USE'

    rows_energy = filtered_df.copy()
    rows_energy.loc[:, 'volume'] = rows_energy['volume']*energy_ratio
    rows_energy.loc[:, 'target'] = 'Energy'

    rows_loss = filtered_df.copy()
    rows_loss.loc[:, 'volume'] = rows_loss['volume']*loss_ratio
    rows_loss.loc[:, 'target'] = 'Loss'

    result_df = pd.concat(
        [dataframe, rows_short_term, rows_energy, rows_loss], ignore_index=True)

    return result_df


def sawmills_inventory_changes(treindustrien_df):

    treindustrien_df.sort_values(['Category', 'year'])
    inventory_df = treindustrien_df[treindustrien_df['Category'].str.contains(
        'inventory', case=False, na=False)]
    inventory_df.loc[:, 'VolumeChange'] = inventory_df.groupby('Category')[
        'Volume'].diff()

    return inventory_df



def create_flow_rows(trade_df, sankey_df, treindustrien_production, treindustrien_inventory):
    # Determine the complete set of years from production
    all_years = sorted(treindustrien_production["year"].unique())
    
    # Production data
    prod = treindustrien_production.reset_index(drop=True)
    year = prod["year"]
    sawn_vol = prod["Volume_sawn"]
    planed_vol = prod["Volume_planed"]
    imp_vol = prod["Volume_imp"]
    
    # Inventory processing (assuming sawmills_inventory_changes exists)
    # NB: each inventory Series must be reindexed on `all_years` so that it
    # aligns with the other per-year Series by year, not by positional index.
    # Without this reindex pandas would align by default integer index, which
    # silently mixes data from different years when the source DataFrames
    # start in different years.
    inventory_df = sawmills_inventory_changes(treindustrien_inventory).reset_index(drop=True)
    inventory_timber = (
        inventory_df[inventory_df['Category'] == 'Timber inventory']
        .groupby('year', as_index=False)['VolumeChange']
        .sum()
        .set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    inventory_sawn = (
        inventory_df[inventory_df['Category'] == 'sawn timber inventory']
        .groupby('year', as_index=False)['VolumeChange']
        .sum()
        .set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    
    # Constants
    SHRINKAGE = 0.05
    SAWDUST = 0.14
    CELLULOSE = 0.26
    SCRAPS = 0.05
    BYPRODUCT_PLANING = 0.05
    # Residual yield of sawn timber from a sawtimber log (mass-balance).
    # Share of initial sawtimber input that becomes sawn timber after
    # shrinkage, sawdust, cellulose chips, and scraps are removed.
    SAWN_YIELD = 1.0 - SHRINKAGE - SAWDUST - CELLULOSE - SCRAPS  # = 0.50
    
    # Reindex trade data for Import Sawn Wood
    import_sawn_wood_df = (
        trade_df[trade_df['source'] == 'Import sawn wood']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    import_sawn_wood_df = (
        import_sawn_wood_df.set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    import_sawn_wood_series = import_sawn_wood_df["volume"]
    
    # Reindex trade data for Export Sawn Wood.
    # NB: in `assign_target_source_trade` the 'sawn wood' eksport entry maps
    # to (source='Sawmills', target='Export sawn wood') — i.e. the label
    # 'Export sawn wood' lives in the `target` column, not `source`. Filtering
    # on `source` silently returned an empty frame, which made the
    # `- export_sawn_wood_series` term in the `left_over_volume` mass balance
    # never fire and double-counted processed sawn-wood exports as a Sawmills
    # outflow (once implicitly via WPP, once explicitly via the trade row).
    export_sawn_wood_df = (
        trade_df[trade_df['target'] == 'Export sawn wood']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    export_sawn_wood_df = (
        export_sawn_wood_df.set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    export_sawn_wood_series = export_sawn_wood_df["volume"]
    
    # Reindex trade data for Import Planed Wood
    import_planed_wood_df = (
        trade_df[trade_df['source'] == 'Import planed wood']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    import_planed_wood_df = (
        import_planed_wood_df.set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    import_planed_wood_series = import_planed_wood_df["volume"]
    
    # Reindex trade data for Export Planed Wood.
    # Same column-side bug as Export sawn wood above: in the trade mapping,
    # 'planed wood' eksport maps to (source='Planingmills', target='Export
    # planed wood'), so the label lives in `target`. Filtering on `source`
    # silently returned zero and made the Planingmills mass balance wrong.
    export_planed_wood_df = (
        trade_df[trade_df['target'] == 'Export planed wood']
        .groupby('year', as_index=False)['volume']
        .sum()
    )
    export_planed_wood_df = (
        export_planed_wood_df.set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    export_planed_wood_series = export_planed_wood_df["volume"]
    
    # Sankey data for initial timber volume.
    # At the point this function is called, sankey_df only contains SSB-07410
    # sawtimber inflows (2006-2024), while the production / inventory Series
    # span the Treindustrien years (2011-2023). Reindex by year against
    # `all_years` so that all downstream arithmetic aligns by year rather
    # than by positional index.
    sankey = (
        sankey_df[sankey_df['target'] == 'Sawmills']
        .groupby("year", as_index=False)["volume"]
        .sum()
        .set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )

    # Raw sawtimber logs that are re-exported without being sawn (SSB 08801
    # 'Skurtømmer eksport'). In the trade mapping these appear as
    # (source='Sawmills', target='Export Sawmills'), so they arrive at the
    # Sawmills node via SSB 07410 but leave again as raw logs without passing
    # through the sawing process. They must be subtracted from the volume
    # entering the saw so that SAWN_YIELD and the byproduct coefficients are
    # applied only to the portion that is actually sawn.
    export_sawmills_df = (
        trade_df[trade_df['target'] == 'Export Sawmills']
        .groupby('year', as_index=False)['volume']
        .sum()
        .set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()
    )
    export_sawmills_series = export_sawmills_df["volume"]

    import_saw_wood_series = (
        trade_df[trade_df['source'] == 'Import saw wood']
        .groupby('year', as_index=False)['volume']
        .sum()
        .set_index('year')
        .reindex(all_years, fill_value=0)
        .reset_index()["volume"]
    )

    # Calculate available timber volume that actually enters the saw.
    #
    #   initial_timber_vol = domestic harvest + imports
    #                        - stock change (inventory_timber)
    #                        - raw sawtimber re-exported as logs
    #
    # Without the ``- export_sawmills_series`` term the raw-log re-exports
    # (~1.3 Mm3/yr) are double-counted as Sawmills outflows: once implicitly,
    # because the full 5.7 Mm3 inflow is spread across byproducts + sawn
    # products via SAWN_YIELD, and once explicitly, because the trade row
    # `Sawmills -> Export Sawmills` is also added to the Sankey.
    initial_timber_vol = (
        sankey["volume"]
        - inventory_timber["VolumeChange"]
        + import_saw_wood_series
        - export_sawmills_series
    )
    
    # Compute water loss (shrinkage) from the raw input timber volume.
    water_vol = initial_timber_vol * SHRINKAGE

    # Calculate byproduct volumes as fractions of the raw INPUT timber volume.
    # The coefficients SAWDUST, CELLULOSE and SCRAPS are defined as shares of
    # the sawtimber log volume entering the sawmill (the usual literature
    # convention, e.g. ~26% cellulose chip yield). Applying them to a
    # post-shrinkage "dry" volume would silently scale every byproduct flow
    # by (1 - SHRINKAGE) and under-report the pulpmill share.
    sawdust_vol = SAWDUST * initial_timber_vol
    cellulose_vol = CELLULOSE * initial_timber_vol
    scraps_vol = SCRAPS * initial_timber_vol
    
    # Mass balance at the Sawmills node for the sawn-timber splits:
    #   sawn_vol_implied = sawn_vol_to_planed
    #                    + export_sawn_wood_series       (processed sawn export)
    #                    + inventory_sawn["VolumeChange"] (Sawmills_stock buildup)
    #                    + left_over_volume              (residual to WPP)
    #
    # Solving for the residual:
    #   left_over_volume = sawn_vol_implied
    #                    - sawn_vol_to_planed
    #                    - export_sawn_wood_series
    #                    - inventory_sawn["VolumeChange"]
    #
    # Notes:
    # * `import_sawn_wood_series` is intentionally NOT added here. Imported
    #   sawn wood flows directly `Import sawn wood -> Wood Product processing
    #   & Logistics` per the trade-mapping table; it does not pass through
    #   the Norwegian Sawmills node, so adding it would fabricate a phantom
    #   Sawmills outflow with no matching inflow.
    # * `inventory_sawn["VolumeChange"]` is subtracted (not added). A positive
    #   VolumeChange represents stock buildup, i.e. an outflow from Sawmills
    #   into Sawmills_stock, so it must reduce — not increase — the Sawmills
    #   -> WPP residual. The stock edge itself is emitted separately.
    
    sawn_vol_implied = initial_timber_vol * SAWN_YIELD
    sawn_vol_to_planed = planed_vol * 1.05
    # The remaining sawn timber (after planed throughput, stock changes and
    # net trade) flows to Wood Product processing & Logistics. Using
    # `sawn_vol_implied` here enforces mass balance at the Sawmills node.
    left_over_volume = (
        sawn_vol_implied
        - sawn_vol_to_planed
        - export_sawn_wood_series
        - inventory_sawn["VolumeChange"]
    )
    
    planed_byproducts = sawn_vol_to_planed * BYPRODUCT_PLANING
    sum_planed_wood = planed_vol + import_planed_wood_series - export_planed_wood_series
    planed_wpp = sum_planed_wood - imp_vol

    # Reset indices to ensure alignment
    year = year.reset_index(drop=True)
    sawn_vol_to_planed = sawn_vol_to_planed.reset_index(drop=True)
    left_over_volume = left_over_volume.reset_index(drop=True)
    water_vol = water_vol.reset_index(drop=True)
    sawdust_vol = sawdust_vol.reset_index(drop=True)
    cellulose_vol = cellulose_vol.reset_index(drop=True)
    scraps_vol = scraps_vol.reset_index(drop=True)
    planed_byproducts = planed_byproducts.reset_index(drop=True)
    planed_wpp = planed_wpp.reset_index(drop=True)
    imp_vol = imp_vol.reset_index(drop=True)
    
    # Build flows for each production year
    flows_list = []
    n = len(year)
    for i in range(n):
        flows_list.extend([
            {"year": year.iloc[i], "volume": sawn_vol_to_planed.iloc[i], 
             "source": "Sawmills", "target": "Planingmills", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": left_over_volume.iloc[i],  
             "source": "Sawmills", "target": "Wood Product processing & Logistics", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": water_vol.iloc[i],  
             "source": "Sawmills", "target": "Shrinkage", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": sawdust_vol.iloc[i],  
             "source": "Sawmills", "target": "Boardmills", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": cellulose_vol.iloc[i],  
             "source": "Sawmills", "target": "Pulpmills", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": scraps_vol.iloc[i],  
             "source": "Sawmills", "target": "Energy", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": planed_byproducts.iloc[i], 
             "source": "Planingmills", "target": "Boardmills", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": planed_wpp.iloc[i],  
             "source": "Planingmills", "target": "Wood Product processing & Logistics", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": imp_vol.iloc[i],  
             "source": "Planingmills", "target": "Impregnation", 
             "origin": "Treindustrien årsrapport"},
            {"year": year.iloc[i], "volume": imp_vol.iloc[i],  
             "source": "Impregnation", "target": "Wood Product processing & Logistics", 
             "origin": "Treindustrien årsrapport"}
        ])
        
    return pd.DataFrame(flows_list)


def create_treindustrien_inventory_rows(treindustrien_df):
    inventory_mapping = {
        'Timber inventory': {
            'positive': ('Sawmills', 'Timber Inventory'),
            'negative': ('Timber Inventory', 'Sawmills')
        },
        'sawn timber inventory': {
            'positive': ('Sawmills', 'Timber Inventory'),
            'negative': ('Timber Inventory', 'Sawmills')
        },
        'planed timber inventory': {
            'positive': ('Planingmills', 'planed timber inventory'),
            'negative': ('planed timber inventory', 'Planingmills')
        },
        'Impregnated inventory': {
            'positive': ('Impregnation', 'Impregnated inventory'),
            'negative': ('Impregnated inventory', 'Impregnation')
        }
    }

    treindustrien_df['source'], treindustrien_df['target'] = zip(
        *treindustrien_df.apply(get_source_target_treindustrien, axis=1))
    treindustrien_df_inventory = treindustrien_df[treindustrien_df['Category'].isin(
        list(inventory_mapping.keys()))].reset_index().reset_index(drop=True)
    treindustrien_df_inventory = treindustrien_df_inventory.dropna()

    # `source`/`target` already encode direction based on the sign of
    # VolumeChange (see get_source_target_treindustrien). The Sankey link
    # width itself must be non-negative, so take the absolute magnitude here.
    treindustrien_rows_inventory = pd.DataFrame({

        'year': treindustrien_df_inventory['year'],
        'volume': treindustrien_df_inventory['VolumeChange'].abs(),
        'source': treindustrien_df_inventory['source'],
        'target': treindustrien_df_inventory['target'],
        'origin': 'Treindustrien årsrapport'
    })

    return treindustrien_rows_inventory


def make_flow(df, volume_col='volume', year_col='year', source_col='source', target_col='target', origin='SSB table 11181'):
    """
    Given a DataFrame `df` with columns [volume_col, year_col, source_col, target_col],
    create a new DataFrame representing a flow from the source to the target for each row.
    """
    return pd.DataFrame({
        'source': df[source_col],
        'target': df[target_col],
        'volume': df[volume_col].astype(float),
        'year':   df[year_col].astype(int),
        'origin': origin
    })


def create_firewood_rows(tbl_11181, percent_spruce=50.0):
    """
    Splits 'firewood_coniferous' data into spruce & pine flows based on `percent_spruce`
    and also processes non-coniferous firewood. Returns all flows as a single DataFrame.

    Parameters
    ----------
    tbl_11181 : pd.DataFrame
        Must include columns:
          - 'value' (firewood volume in 1000 m³ before scaling)
          - 'år' (year)
          - 'virkesgruppe' (e.g. 'Ved til brensel lauv' or other coniferous labels)
    percent_spruce : float (0-100), default=50
        Percentage of coniferous volume to assign to 'Spruce'; remainder goes to 'Pine'.

    Returns
    -------
    pd.DataFrame
        A concatenated DataFrame with flows:
          - Spruce → Firewood
          - Standing wood → Spruce
          - Pine → Firewood
          - Standing wood → Pine
          - Non-coniferous → Firewood
          - Standing wood → Non-coniferous
        Columns: [source, target, volume, year, origin]
    """
    # 1) Convert from 1000 m³ to m³
    df = tbl_11181.copy()
    df['value'] = df['value'] * 1000

    # 2) Split between coniferous and non-coniferous
    conifer_mask = (df['virkesgruppe'] != 'Ved til brensel lauv')
    firewood_coniferous = df[conifer_mask]
    non_coniferous_firewood_df = df[~conifer_mask]  # 'Ved til brensel lauv'

    # 3) Define ratio
    spruce_ratio = percent_spruce / 100.0
    pine_ratio = 1 - spruce_ratio

    # 4) Build flows for Spruce & Pine in a loop
    #    Each species needs two flows:
    #        species_name -> Firewood, and
    #        Standing wood -> species_name
    flows_list = []

    for species_name, ratio in [('Spruce', spruce_ratio), ('Pine', pine_ratio)]:
        subdf = firewood_coniferous.assign(
            value=firewood_coniferous['value'] * ratio)
        flows_list.append(make_flow(subdf, species_name, 'Firewood'))
        flows_list.append(make_flow(subdf, 'Standing wood', species_name))

    # 5) Build flows for Non-coniferous (ratio = 1, just the subset)
    #    Also the same pattern: Non-coniferous -> Firewood, Standing wood -> Non-coniferous
    flows_list.append(make_flow(non_coniferous_firewood_df,
                      'Non-coniferous', 'Firewood'))
    flows_list.append(make_flow(non_coniferous_firewood_df,
                      'Standing wood', 'Non-coniferous'))

    # 6) Concatenate all flow DataFrames
    return pd.concat(flows_list, ignore_index=True)


def assign_target_source_trade(trade_df):
    """
    Assign source and target labels to each trade record in a DataFrame based on
    its trade type and direction.

    This function uses a predefined mapping of trade types and their corresponding
    source/target pairs for both import and eksport (export) directions. It expects
    the input DataFrame to contain at least the following columns:
        - 'type': A string indicating the trade type (e.g., 'impregnated', 'pulpwood').
        - 'import/eksport': A string indicating the trade direction ('import' or 'eksport').

    The function normalizes these values by stripping any extra whitespace and converting
    them to lower case before performing the lookup. If a combination of trade type and
    direction is not found in the mapping, the resulting 'source' and 'target' values for that
    record will be set to None.

    Parameters
    ----------
    trade_df : pd.DataFrame
        A pandas DataFrame containing trade data with at least the 'type' and 'import/eksport'
        columns.

    Returns
    -------
    pd.DataFrame
        The original DataFrame with two new columns:
            - 'source': The source label determined from the mapping.
            - 'target': The target label determined from the mapping.
    """
    # Predefined mapping of trade types and directions to (source, target) tuples.
    trade_mapping = {
        'impregnated': {
            'import': ('Import impregnated', 'Wood Product processing & Logistics'),
            'eksport': ('Impregnation', 'Export Impregnation')
        },
        'pulpwood': {
            'import': ('Import pulpwood', 'Pulpmills'),
            'eksport': ('Pulpmills', 'Export pulpwood')
        },
        'saw wood': {
            'import': ('Import saw wood', 'Sawmills'),
            'eksport': ('Sawmills', 'Export Sawmills')
        },
        'pellets': {
            'import': ('Import pellets', 'Firewood'),
            'eksport': ('Sawmills', 'Export pellets')
        },
        'firewood': {
            'import': ('Import Firewood', 'Firewood'),
            'eksport': ('Firewood', 'Export Firewood')
        },
        'planed wood': {
            'import': ('Import planed wood', 'Wood Product processing & Logistics'),
            'eksport': ('Planingmills', 'Export planed wood')
        },
        'sawn wood': {
            'import': ('Import sawn wood', 'Wood Product processing & Logistics'),
            'eksport': ('Sawmills', 'Export sawn wood')
        }
    }

    def get_source_target(row):
        """
        Determine the source and target for a single trade record.

        This helper function takes a row from the DataFrame, normalizes the 'type' and
        'import/eksport' columns by stripping whitespace and converting them to lower case,
        and then uses the predefined mapping to obtain the corresponding (source, target)
        tuple. If the lookup fails (for instance, if the trade type or direction is not
        present in the mapping), it returns (None, None).

        Parameters
        ----------
        row : pd.Series
            A row from the DataFrame containing at least the 'type' and 'import/eksport' values.

        Returns
        -------
        pd.Series
            A pandas Series with two elements:
                - The source label.
                - The target label.
        """
        # Normalize the 'type' and 'import/eksport' fields
        trade_type = row['type'].strip().lower()
        direction = row['import/eksport'].strip().lower()

        # Attempt to retrieve the corresponding (source, target) tuple from the mapping.
        try:
            source, target = trade_mapping[trade_type][direction]
        except KeyError:
            # If the mapping for the given trade type or direction is not found, assign None.
            source, target = None, None

        return pd.Series([source, target])

    # Apply the helper function to each row and create new 'source' and 'target' columns.
    trade_df[['source', 'target']] = trade_df.apply(get_source_target, axis=1)


    return trade_df
