import plotly.graph_objects as go
import pandas as pd
import json
from pathlib import Path

root_dir = Path(__file__).parent

print("Current working directory:", root_dir)


# ---------------------------------------------------------------------------
# Node-name normalisation
# ---------------------------------------------------------------------------
# sankey_data.csv (regenerated from Volume_dataset) uses slightly different
# node labels than sankey_params.json. Remap the data-side names onto the
# params-side labels so the two sides line up. Nodes without a mapping here
# fall through unchanged, and any flow whose source/target does not appear in
# sankey_params.json is dropped in ``remap_ids`` via ``dropna``.
NODE_RENAME_MAP = {
    # Case-only differences
    "Standing Wood": "Standing wood",
    "End-of-Life": "End-of-life",
    "Material recovery": "Material Recovery",

    # Inventory/stock nodes produced by the Treindustrien pipeline
    "Timber Inventory": "Sawmills_stock",
    "planed timber inventory": "Planingmills_stock",
    "Impregnated inventory": "Boardmills_stock",

    # Export nomenclature
    "Export sawn wood": "Export Sawn",
    "Export Sawmills": "Export Sawn",
    "Export planed wood": "Export Planed",
}


def drop_nodes(df, categories_to_drop, label_column='label'):
    """
    Drops rows from a dataframe based on a list of categories.
    """
    return df[~df[label_column].isin(categories_to_drop)]


def load_node_params(param_file_path, nodes_to_drop):
    """
    Load node parameters and drop specified nodes.
    """
    with open(param_file_path, 'r') as f:
        nodes_data = json.load(f)

    nodes_df = drop_nodes(pd.DataFrame(
        nodes_data), categories_to_drop=nodes_to_drop, label_column='label')

    return (nodes_df['label'].tolist(),
            nodes_df['x_position'].tolist(),
            nodes_df['y_position'].tolist(),
            nodes_df['node_color'].tolist())


def load_and_filter_data(data_file_path, nodes_to_drop, min_year, max_year, sankey_choice):
    """
    Load input data, filter nodes and years, and optionally compute mean flows.

    The input schema (sankey_data.csv) has columns:
        source, target, volume, year, origin
    Volumes are stored in m3 and are converted to million m3 (Mm3) on load so
    that the Sankey diagram displays values in the same units used in the
    paper.
    """
    df = pd.read_csv(data_file_path)

    # Normalise column names defensively (strip any BOM on 'source').
    df.columns = [c.lstrip('\ufeff').strip() for c in df.columns]

    # Drop rows missing year/volume (any malformed entries in the source file).
    df = df.dropna(subset=['year', 'volume'])

    # --- Unit conversion: m3 -> Mm3 (million m3) ---
    df['volume'] = df['volume'].astype(float) / 1_000_000.0

    # --- Node-name normalisation to match sankey_params.json labels ---
    df['source'] = df['source'].replace(NODE_RENAME_MAP)
    df['target'] = df['target'].replace(NODE_RENAME_MAP)

    # Drop nodes from both source and target
    df = drop_nodes(df, categories_to_drop=nodes_to_drop,
                    label_column='target')
    df = drop_nodes(df, categories_to_drop=nodes_to_drop,
                    label_column='source')

    df['year'] = df['year'].astype(int)
    df = df[(df['year'] >= min_year) & (df['year'] <= max_year)]

    # Two-stage aggregation (SUM within year, then MEAN across years).
    #
    # The upstream dedupe pass in Volume_dataset/volume.py already collapses
    # `sankey_data.csv` to one row per (year, source, target). The NODE_RENAME_MAP
    # above, however, can merge distinct nodes (e.g. `Export Sawmills` (raw
    # sawtimber export) and `Export sawn wood` (processed export) both fold
    # into the paper's `Export Sawn` bucket). After the rename those become
    # two rows for the same (year, source, target) again — and the correct
    # aggregation is SUM (they are parallel components of the same bucket),
    # not MEAN.
    #
    # Only after the per-year SUM do we average across the year window to
    # produce the "annual average 2012-2022" figure published in the paper.
    df = (
        df.groupby(['source', 'target', 'year'], as_index=False)['volume']
          .sum()
    )
    if sankey_choice == 'avg':
        df = (
            df.groupby(['source', 'target'], as_index=False)['volume']
              .mean()
        )

    return df


def remap_ids(df, labels):
    """
    Remap source_id and target_id based on filtered labels.
    Flows referencing a node not present in ``labels`` are dropped.
    """
    label_to_index = {label: i for i, label in enumerate(labels)}
    df = df.copy()
    df['source_id'] = df['source'].map(label_to_index)
    df['target_id'] = df['target'].map(label_to_index)
    df = df.dropna(subset=['source_id', 'target_id'])
    df[['source_id', 'target_id']] = df[['source_id', 'target_id']].astype(int)
    return df


def create_sankey_figure(labels, x_positions, y_positions, node_colors, df,
                         min_year, max_year, value_column='volume',
                         value_unit='Mm\u00b3'):
    """
    Create a Sankey diagram figure using Plotly.

    Parameters
    ----------
    value_column : str
        Column in ``df`` used for the link thickness. Defaults to ``'volume'``
        (in Mm3 after the conversion performed in ``load_and_filter_data``).
    value_unit : str
        Unit string shown in flow labels and hover text. Defaults to ``'Mm\u00b3'``.
    """
    df = df.sort_values(['source_id', 'target_id']).reset_index(drop=True)

    values = df[value_column].abs().tolist()

    # Flow annotations: one label per link, formatted as the paper's Figure 3
    # (e.g. "1.6", "4.1"). Flows smaller than 0.05 Mm3 are left blank to avoid
    # cluttering the diagram.
    link_labels = [
        f"{v:.1f}" if v >= 0.05 else ""
        for v in values
    ]

    link = dict(
        source=df['source_id'].tolist(),
        target=df['target_id'].tolist(),
        value=values,
        label=link_labels,
        hovertemplate=(
            "%{source.label} \u2192 %{target.label}<br>"
            "%{value:.2f} " + value_unit + "<extra></extra>"
        ),
    )
    node = dict(
        label=labels,
        color=node_colors,
        x=x_positions,
        y=y_positions,
        pad=15,
        thickness=20,
        align='right',
        hovertemplate=(
            "%{label}<br>Total: %{value:.2f} " + value_unit + "<extra></extra>"
        ),
    )

    with open(root_dir / "nodes_output.json", "w") as f:
        json.dump(node, f)

    fig = go.Figure(go.Sankey(
        textfont=dict(color="black", size=12),
        arrangement='snap',
        node=node,
        link=link,
        valueformat='.2f',
        valuesuffix=f' {value_unit}',
    ))
    fig.update_layout(
        title_text=(
            f'Wood flows in Norway, annual average {min_year}-{max_year} '
            f'({value_unit})'
        ),
        font_size=10,
    )
    return fig


def main():
    sankey_choice = 'avg'
    min_year = 2012
    max_year = 2022
    nodes_to_drop = ['Energy output']

    param_file_path = root_dir / 'sankey_params.json'
    data_file_path = root_dir / 'sankey_data.csv'

    labels, x_positions, y_positions, node_colors = load_node_params(
        param_file_path, nodes_to_drop)
    df_values = load_and_filter_data(
        data_file_path, nodes_to_drop, min_year, max_year, sankey_choice)
    df_values = remap_ids(df_values, labels)
    fig = create_sankey_figure(
        labels, x_positions, y_positions, node_colors, df_values,
        min_year, max_year)
    fig.show()

    # Optional: persist the figure as a standalone HTML. Uncomment to use.
    # out_path = root_dir / f'figures/sankey_{min_year}_{max_year}.html'
    # out_path.parent.mkdir(parents=True, exist_ok=True)
    # fig.write_html(str(out_path))


if __name__ == '__main__':
    main()
