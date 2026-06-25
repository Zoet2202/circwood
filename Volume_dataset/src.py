import os
import pandas as pd
import pyjstat
import json
from pyjstat import pyjstat
import requests


def process_query_files(directory_query_path):
    """
    Process JSON query files in a specified directory and return the results as a dictionary.

    Parameters:
    directory_query_path (str): The path to the directory containing the JSON query files.

    Returns:
    dict: A dictionary where the keys are table IDs and the values are pyjstat objects.
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


def density_calculator(species, moisture_content):
    mc_percent = moisture_content/100
    if species == "spruce":
        avg_dry_density = 430
        FSP = 32/100
        max_volume_shrinkage = 11.7/100
    elif species == "pine":
        avg_dry_density = 490
        FSP = 27/100
        max_volume_shrinkage = 12.1/100
    elif species == "birch":
        avg_dry_density = 580
        FSP = 34/100
        max_volume_shrinkage = 17.4/100
    Density_at_given_mc = (
        100-mc_percent*max_volume_shrinkage/FSP)*(1+mc_percent)/100*avg_dry_density

    return Density_at_given_mc


def mortality(Forest_growth, rot_factor):
    # Sort the DataFrame by 'species' and 'year'
    Forest_growth = Forest_growth.sort_values(
        by=['species', 'year']).reset_index(drop=True)

    # Initialize deadbiomass_stock column
    Forest_growth['deadbiomass_stock'] = 0.0

    # Group by 'species' and apply the mortality calculation for each group
    def calculate_stock(group):
        for i in range(len(group)):
            if i == 0:
                group.iloc[i, group.columns.get_loc(
                    'deadbiomass_stock')] = group.iloc[i]['deadbiomass m3']
            else:
                previous_stock = group.iloc[i-1]['deadbiomass_stock']
                current_deadbiomass = group.iloc[i]['deadbiomass m3']
                group.iloc[i, group.columns.get_loc(
                    'deadbiomass_stock')] = previous_stock * (1 - rot_factor) + current_deadbiomass
        return group

    # Apply the calculation to each group
    Forest_growth = Forest_growth.groupby('species').apply(
        calculate_stock).reset_index(drop=True)

    return Forest_growth


def read_excel_to_dataframe(filepath, sheet_name=0):
    """
    Reads an Excel file and returns it as a pandas DataFrame.

    Parameters:
    file_path (str): The path to the Excel file.
    sheet_name (str or int, optional): The sheet name or index to read. Defaults to 0 (the first sheet).

    Returns:
    pd.DataFrame: The DataFrame containing the data from the Excel file.
    """
    try:
        # Read the Excel file
        df = pd.read_excel(filepath, sheet_name=sheet_name)
        return df
    except Exception as e:
        print(f'Error reading the Excel file: {e}')
        return None


def sawing(data, ids):
    """
    Calculate the volume distribution for different products for each year based on each category in a pandas DataFrame.

    Parameters:
    data (pd.DataFrame): The input data as a pandas DataFrame.
    ids (list): List of IDs to process.

    Returns:
    pd.DataFrame: The original DataFrame with the calculated values appended.
    """

    # Define the product percentages
    biproducts_ratio = 0.51 / 0.45
    product_percentages = {
        'sawn wood': 0.51,
        'firewood': 0.14 * biproducts_ratio,
        'cellulose': 0.26 * biproducts_ratio,
        'shrinkage': 0.05,
        'scraps': 0.05 * biproducts_ratio
    }

    results_df = pd.DataFrame(columns=['year', 'species', 'product', 'value'])

    # Filter the data to include only the rows with specified IDs
    filtered_data = data[data['ID'].isin(ids)]

    # Get unique species and years from filtered data
    species_list = filtered_data['sortiment'].unique()
    years_list = filtered_data['år'].unique()

    for species in species_list:
        filtered_species = filtered_data[filtered_data['sortiment'] == species]
        for year in years_list:
            filtered_df = filtered_species[filtered_species['år'] == year]
            yearly_sum = filtered_df['volume (m3)'].sum()

            # Create rows for each product
            for product, percentage in product_percentages.items():
                product_value = yearly_sum * percentage
                product_row = pd.DataFrame({
                    'year': [year],
                    'species': [species],
                    'product': [product],
                    'value': [product_value]
                })

                # Append the new row to the results DataFrame using pd.concat
                results_df = pd.concat(
                    [results_df, product_row], ignore_index=True)

    # Append the results DataFrame to the original DataFrame
    data = pd.concat([data, results_df], ignore_index=True)

    return data


def planing(data):
    """
    Calculate the volume distribution for different products for each year based on each category.

    Parameters:
    data (pd.DataFrame): The input data as a pandas DataFrame.
    We assume 14.40% loss in terms of biproducts that are used in the boardmill industry

    Returns:
    pd.DataFrame: A DataFrame with years, species, product, and the calculated values.
    """

    product_percentages = {
        'planed timber': 1-0.144,
        'byproduct': 0.144,
    }

    results_df = pd.DataFrame(columns=['year', 'species', 'product', 'value'])

    # Get unique species and years
    species_list = data['sortiment'].unique()
    years_list = data['år'].unique()

    for species in species_list:
        filtered_species = data[data['sortiment'] == species]
        for year in years_list:
            filtered_df = filtered_species[filtered_species['år'] == year]
            yearly_sum = filtered_df['volume (m3)'].sum()

            # Create rows for each product
            for product, percentage in product_percentages.items():
                product_value = yearly_sum * percentage
                product_row = pd.DataFrame({
                    'year': [year],
                    'species': [species],
                    'product': [product],
                    'value': [product_value]
                })

                # Append the new row to the results DataFrame using pd.concat
                results_df = pd.concat(
                    [results_df, product_row], ignore_index=True)

    return results_df


def impregnation(data):
    """
    Calculate the volume distribution for different products for each year based on each category.

    Parameters:
    data (pd.DataFrame): The input data as a pandas DataFrame.
    We assume 14.40% loss in terms of biproducts that are used in the boardmill industry

    Returns:
    pd.DataFrame: A DataFrame with years, species, product, and the calculated values.
    """

    product_percentages = {
        'impregnated timber': 1-0.144,
        'byproduct': 0.144,
    }

    results_df = pd.DataFrame(columns=['year', 'species', 'product', 'value'])

    # Get unique species and years
    species_list = data['sortiment'].unique()
    years_list = data['år'].unique()

    for species in species_list:
        filtered_species = data[data['sortiment'] == species]
        for year in years_list:
            filtered_df = filtered_species[filtered_species['år'] == year]
            yearly_sum = filtered_df['volume (m3)'].sum()

            # Create rows for each product
            for product, percentage in product_percentages.items():
                product_value = yearly_sum * percentage
                product_row = pd.DataFrame({
                    'year': [year],
                    'species': [species],
                    'product': [product],
                    'value': [product_value]
                })

                # Append the new row to the results DataFrame using pd.concat
                results_df = pd.concat(
                    [results_df, product_row], ignore_index=True)

    return results_df


def firewood_ratio(percent_spruce):
    spruce = float(percent_spruce/100)
    pine = float((1-spruce))

    return spruce, pine


def assign_source_trade(row, species):

    varenummer_lower = row['varenummer'].lower()
    net_export = row['net exports']

    if species in ['gran', 'furu']:
        product_map_source = {
            'massevirke': ('Spruce', 'Import Pulpmills'),
            'skurtømmer': ('Spruce', 'Import Sawmills'),
            'tømmer': ('Spruce', 'Import Boardmills'),
            'impregnert': ('Impregnation', 'Import Impregnated'),
            'uimpregnert': ('Sawmills', 'Import Sawn')
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

    varenummer_lower = row['varenummer'].lower()
    net_export = row['net exports']

    if species in ['gran', 'furu']:
        product_map_target = {
            'massevirke': ('Export', 'Pulpmills'),
            'skurtømmer': ('Export', 'Sawmills'),
            'tømmer': ('Export', 'Boardmills'),
            'impregnert': ('Export', 'Impregnation'),
            'uimpregnert': ('Export', 'Sawmills')
        }
    elif species in ['lauv', 'bjørk', 'brensel']:
        product_map_target = {
            'brensel': ('Export', 'Import Firewood'),
            'lauv': ('Export', 'Import Firewood'),
            'bjørk': ('Export', 'Import Firewood')
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
