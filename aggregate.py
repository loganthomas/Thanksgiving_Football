"""
Data aggregation functions
"""
# Standard libraries
from pathlib import Path
from typing import Any, Dict

# Third-party libraries
import numpy as np
import pandas as pd

# Local libraries
import utils


def _get_player_pnts_stat_type(player_pts: Dict[str, Any]) -> str:
    """
    Helper function to get the stat type within the pulled player
    points. Will be either 'projectedStats' or 'stats'.
    """
    unique_type = set([list(v.keys())[0] for v in player_pts.values()])

    if len(unique_type) > 1:
        raise ValueError(f"More than one unique stats type detected: {unique_type}")

    stats_type = list(unique_type)[0]

    if stats_type not in ("projectedStats", "stats"):
        raise ValueError(
            f"Unrecognized stats type: '{stats_type}'. Expected either 'projectedStats' or 'stats'."
        )

    return stats_type


def _unpack_player_pnts(
    year: int, week: int, player_pts_dict: Dict[str, Any]
) -> Dict[str, str]:
    """
    Helper function to unpack player points nested dictionaries.
    """
    ((_, points_dict),) = player_pts_dict.items()
    points_dict = points_dict.get("week").get(str(year)).get(str(week))

    return points_dict


def create_player_pts_df(
    year: int, week: int, player_pts: Dict[str, Any]
) -> pd.DataFrame:
    """
    Create a DataFrame to house all player projected and actual points.
    The ``player_pts`` argument can be ``projected_player_pts`` or
    ``actual_player_pts``.
    """
    # Determine whether provided projected ('projectedStats') or
    # actual player points ('stats'). Actual points will need to
    # be pulled multiple times throughout as more games are
    # played/completed. Projected points should be pulled once and
    # only once.
    stats_type = _get_player_pnts_stat_type(player_pts)

    proj_filename = Path(f"archive/{year}").joinpath(
        f"{year}_{week}_projected_player_pts.csv"
    )

    if (stats_type == "projectedStats") & (proj_filename.exists()):
        print(f"Projected player data already exists at: {proj_filename}")
        player_pts_df = pd.read_csv(proj_filename, index_col=0)

    else:

        index = []
        points = []

        for pid, player_pnts_dict in player_pts.items():
            points_dict = _unpack_player_pnts(year, week, player_pnts_dict)
            index.append(pid)
            points.append(points_dict)

        player_pts_df = pd.DataFrame(points, index=index)

        # Get definition of each point attribute
        stat_ids_json_path = Path("./stat_ids.json")
        stat_ids_dict = utils.load_from_json(stat_ids_json_path)
        stat_defns = {k: v["name"].replace(" ", "_") for k, v in stat_ids_dict.items()}
        player_pts_df = player_pts_df.rename(columns=stat_defns)

        if stats_type == "projectedStats":
            prefix = "PROJ_"

        if stats_type == "stats":
            prefix = "ACTUAL_"

        player_pts_df = player_pts_df.add_prefix(prefix)
        player_pts_df = player_pts_df.reset_index().rename(columns={"index": "Player"})

        # Get definition of each player team and name based on player id
        player_ids_json_path = Path("./player_ids.json")
        player_ids = utils.load_from_json(player_ids_json_path)
        team = player_pts_df["Player"].apply(lambda x: player_ids[x]["team"])
        player_pts_df.insert(1, "Team", team)

        player_defns = {k: v["name"] for k, v in player_ids.items() if k != "year"}
        name = player_pts_df["Player"].apply(lambda x: player_defns[x])
        player_pts_df["Player"] = name

        # Make pts col the third column for easy access
        pts_col = player_pts_df.filter(regex="pts")
        pts_col_name = f"{prefix}pts"
        player_pts_df = player_pts_df.drop(pts_col_name, axis=1)
        player_pts_df.insert(2, pts_col_name, pts_col)

        # Convert all col types to non-string as strings come from scrape
        col_types = {}
        for c in player_pts_df.columns:
            if c in ("Player", "Team"):
                col_types[c] = "object"
            elif "Games_Played" in c:
                col_types[c] = "int64"
            else:
                col_types[c] = "float64"

        player_pts_df = player_pts_df.astype(col_types)

        # Write projected players to csv so only done once
        if stats_type == "projectedStats":

            print(f"\nWriting projected player stats to: {proj_filename}...")
            player_pts_df.to_csv(proj_filename)

    return player_pts_df


def merge_points(
    participant_teams: Dict[str, pd.DataFrame], pts_df: pd.DataFrame
) -> Dict[str, pd.DataFrame]:
    """
    Merge participant team with collected player points.

    ``pts_df`` can be projected or actual points scraped.
    """
    for participant, participant_team in participant_teams.items():

        # Merge points
        merged = pd.merge(participant_team, pts_df, how="left", on=["Player", "Team"])

        # Drop columns where all values are nan
        merged = merged.dropna(axis=1, how="all")

        # Check that all players are found
        not_found_mask = np.where(merged.filter(regex="_pts")[:-1].isna())[0]

        if len(not_found_mask) > 0:
            missing = merged["Player"][not_found_mask].tolist()
            print(f"WARNING: {participant} has missing players: {missing}")

        # Fill remaining nan with 0.0 (must come after check)
        merged = merged.fillna(0.0)

        participant_teams[participant] = merged

    return participant_teams


def sort_robust_cols(
    participant_teams: Dict[str, pd.DataFrame]
) -> Dict[str, pd.DataFrame]:
    """
    Sort projected and actual columns so that they line up post merge
    """
    for participant, participant_team in participant_teams.items():
        orig_cols = list(participant_team.columns)
        new_cols = []

        for c in orig_cols:
            stripped_c = c.replace("PROJ_", "").replace("ACTUAL_", "")

            if (
                (f"PROJ_{stripped_c}" in orig_cols)
                & (f"ACTUAL_{stripped_c}" in orig_cols)
            ) & (
                (f"PROJ_{stripped_c}" not in new_cols)
                & (f"ACTUAL_{stripped_c}" not in new_cols)
            ):
                new_cols.append(f"ACTUAL_{stripped_c}")
                new_cols.append(f"PROJ_{stripped_c}")
            else:
                if c not in new_cols:
                    new_cols.append(c)

        participant_teams[participant] = participant_team[new_cols]

    return participant_teams


def write_robust_participant_team_scores(
    year: int, week: int, participant_teams: Dict[str, pd.DataFrame]
) -> None:
    """
    Writes the total points to an excel file that can be reviewed.
    """
    filename = Path(f"archive/{year}").joinpath(
        f"{year}_{week}_robust_participant_player_pts.xlsx"
    )
    print(f"\nWriting robust player points summary to: {filename}...")

    with pd.ExcelWriter(filename) as writer:
        for participant, participant_team in participant_teams.items():

            # Write data to sheet
            participant_team.to_excel(writer, sheet_name=participant, index=False)

            # Auto-format column lengths and center columns
            worksheet = writer.sheets[participant]
            workbook = writer.book
            center = workbook.add_format()
            center.set_align("center")
            center.set_align("vcenter")

            for i, col in enumerate(participant_team):
                series = participant_team[col]
                max_len = max(series.astype(str).map(len).max(), len(str(series.name)))

                # Add a little extra spacing
                if col in ("Position", "Player", "Team"):
                    worksheet.set_column(i, i, max_len + 2)
                else:
                    worksheet.set_column(i, i, max_len + 2, center)