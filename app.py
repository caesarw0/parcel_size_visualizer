import streamlit as st
import geopandas as gpd
import numpy as np
import pandas as pd
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import io
import json
import os
from datetime import datetime
from cryptography.fernet import Fernet
import branca.colormap as cm


GOOGLE_TILES = {
    "Satellite": {
        "url": "https://mt0.google.com/vt/lyrs=y&hl=en&x={x}&y={y}&z={z}",
        "attr": "Google Hybrid"
    },
    "Terrain Map": {
        "url": "https://mt0.google.com/vt/lyrs=p&hl=en&x={x}&y={y}&z={z}",
        "attr": "Terrain"
    },
}

EXCLUSIONS_FILE = "excluded_parcels.json"


# --- 1. AUTHENTICATION LOGIC ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        if (
            st.session_state["username"] == st.secrets["credentials"]["username"]
            and st.session_state["password"] == st.secrets["credentials"]["password"]
        ):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        if st.button("Login"):
            password_entered()
            st.rerun()
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", key="password")
        st.button("Login")
        st.error("😕 User not found or password incorrect")
        return False
    return True


# --- 2. DATA LOADING (DECRYPTION) ---
@st.cache_data
def load_data():
    # Fetch key from secrets
    key = st.secrets["credentials"]["encryption_key"]
    cipher_suite = Fernet(key.encode() if isinstance(key, str) else key)

    # Read and Decrypt
    with open("parcel_polygon_stat.dat", "rb") as f:
        encrypted_data = f.read()

    decrypted_data = cipher_suite.decrypt(encrypted_data)

    # Load into GeoPandas using io.BytesIO
    # driver="GPKG" stops the "non conformant file extension" warning
    bytes_io = io.BytesIO(decrypted_data)
    gdf = gpd.read_file(bytes_io, driver="GPKG")

    # Ensure CRS is correct for Folium
    if gdf.crs != "EPSG:4326":
        gdf = gdf.to_crs(epsg=4326)

    gdf = gdf.sort_values(by="variance_acres", ascending=False).reset_index(drop=True)
    return gdf


# --- 3. EXCLUSION MANAGEMENT ---
def load_exclusions() -> list:
    """Load excluded parcels from file. Returns empty list if file missing or unreadable."""
    if not os.path.exists(EXCLUSIONS_FILE):
        return []
    try:
        with open(EXCLUSIONS_FILE, "r") as f:
            data = json.load(f)
        return data.get("excluded", [])
    except Exception:
        return []


def save_exclusions(exclusions: list) -> None:
    """Write exclusions list to file. Warns in UI if write fails."""
    try:
        with open(EXCLUSIONS_FILE, "w") as f:
            json.dump({"excluded": exclusions}, f, indent=2)
    except Exception as e:
        st.warning(f"Could not save exclusions to file: {e}. Changes apply this session only.")


def do_exclude_parcels(gdf_display, indices: list) -> None:
    """Add selected rows to the exclusion list and persist to file."""
    existing = st.session_state["exclusions"]
    existing_ids = {e["parcelnumb"] for e in existing}
    new_entries = []
    for idx in indices:
        row = gdf_display.iloc[idx]
        parcelnumb = row["parcelnumb"]
        if parcelnumb not in existing_ids:
            new_entries.append({
                "parcelnumb": parcelnumb,
                "owner": str(row.get("owner", "")),
                "address": str(row.get("address", "")),
                "excluded_at": datetime.now().isoformat(timespec="seconds")
            })
    updated = existing + new_entries
    st.session_state["exclusions"] = updated
    save_exclusions(updated)


def do_restore_parcel(parcelnumb: str) -> None:
    """Remove a single parcel from the exclusion list and persist."""
    updated = [e for e in st.session_state["exclusions"] if e["parcelnumb"] != parcelnumb]
    st.session_state["exclusions"] = updated
    save_exclusions(updated)


def do_restore_all() -> None:
    """Clear all exclusions and persist."""
    st.session_state["exclusions"] = []
    save_exclusions([])


# --- 4. MAIN APP INTERFACE ---
def main_app():
    st.title("🗺️ Parcel Size Variance Explorer")

    gdf = load_data()

    # Compute display columns on full gdf
    gdf["variance_pct_display"] = np.where(
        (gdf["variance_acres"].notnull()) &
        (gdf["variance_acres"] != 0) &
        (gdf["assessor_acres_clean"].notnull()) &
        (gdf["assessor_acres_clean"] != 0),
        gdf["variance_pct"].round(2).astype(str) + "%",
        ""
    )

    gdf["full_mail_address"] = gdf["mailadd"] + ", " + gdf["mail_city"] + ", " + gdf["mail_state2"] + ", " + gdf["mail_zip"]

    # --- Load exclusions into session state ---
    if "exclusions" not in st.session_state:
        st.session_state["exclusions"] = load_exclusions()

    # --- Apply exclusion filter ---
    excluded_ids = {e["parcelnumb"] for e in st.session_state["exclusions"]}
    gdf_display = gdf[~gdf["parcelnumb"].isin(excluded_ids)].reset_index(drop=True)

    # --- Handle row selection for map centering ---
    selected_parcel_id = None
    selection_event = st.session_state.get("data_table")

    if selection_event and len(selection_event.selection.rows) > 0:
        idx = selection_event.selection.rows[0]
        selected_parcel = gdf_display.iloc[idx]
        selected_parcel_id = selected_parcel["parcelnumb"]

        target_lat = selected_parcel.geometry.centroid.y
        target_lon = selected_parcel.geometry.centroid.x

        st.session_state.map_center = [target_lat, target_lon]
        st.session_state.map_zoom = 15

    # --- Vacancy filtering on display dataset ---
    gdf_vacant = gdf_display[gdf_display["vacancy"] == "Y"]
    gdf_not_vacant = gdf_display[(gdf_display["vacancy"] != "Y") | (gdf_display["vacancy"].isna())]

    # --- Initialize Session State for Map ---
    if 'map_center' not in st.session_state:
        avg_lat = gdf_display.geometry.centroid.y.mean()
        avg_lon = gdf_display.geometry.centroid.x.mean()
        st.session_state.map_center = [avg_lat, avg_lon]
        st.session_state.map_zoom = 13

    # --- Layout: Map ---
    st.subheader("Interactive Map")
    m = folium.Map(
        location=st.session_state.map_center,
        zoom_start=st.session_state.map_zoom,
        tiles=None
    )

    # --- Base Layers (radio buttons) ---
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OpenStreetMap",
        overlay=False,
        control=True
    ).add_to(m)

    folium.TileLayer(
        tiles=GOOGLE_TILES["Satellite"]["url"],
        attr=GOOGLE_TILES["Satellite"]["attr"],
        name="Satellite",
        overlay=False,
        control=True
    ).add_to(m)

    # Log normalization
    vmin, vmax = gdf_display["variance_acres"].min(), gdf_display["variance_acres"].max()
    norm = colors.LogNorm(vmin=vmin, vmax=vmax)

    colormap = plt.get_cmap("RdYlGn")

    def get_color(feature):
        val = feature["properties"]["variance_acres"]
        if val <= 0 or np.isnan(val):
            return "#cccccc"  # neutral for invalid
        rgba = colormap(norm(val))
        return colors.to_hex(rgba)

    # Folium legend (log scale approximation)
    legend = cm.linear.RdYlGn_09.scale(vmin, vmax)
    legend.caption = "Variance (Acres) - Log Scaled"
    legend.add_to(m)

    def style_function(feature):
        is_selected = (
            selected_parcel_id is not None and
            feature["properties"]["parcelnumb"] == selected_parcel_id
        )

        return {
            "fillColor": get_color(feature),
            "color": "red" if is_selected else "black",
            "weight": 4 if is_selected else 1,
            "fillOpacity": 0.6 if is_selected else 0.4,
        }

    fields = [
        "parcelnumb", "alt_parcelnumb1", "full_mail_address", "address", "county", "state2", "szip",
        "owner",
        "variance_acres", "variance_pct_display",
        "assessor_acres_clean", "ll_gisacre",
        "usedesc", "zoning", "saleprice",
        "lbcs_function_desc", "lbcs_structure_desc"
    ]
    aliases = [
        "Parcel Number:", "Tax Map #:", "Full Mailing Address:", "Address:", "County:", "State:", "Zip:",
        "Owner:",
        "Variance Acres:", "Variance Percent:",
        "Deeded Acres:", "Calculated Acres:",
        "Used Description:", "Zoning:", "Sale Price:",
        "LBCS Function:", "LBCS Structure:"
    ]
    folium.GeoJson(
        gdf_vacant,
        name="Vacant Parcels",
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(
            fields=fields,
            aliases=aliases
        )
    ).add_to(m)

    folium.GeoJson(
        gdf_not_vacant,
        name="Not Vacant Parcels",
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(
            fields=fields,
            aliases=aliases
        )
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    st_folium(m, width="100%", height=500, key="main_map")

    # --- Layout: Table ---
    n_total = len(gdf)
    n_excluded = len(st.session_state["exclusions"])
    n_showing = len(gdf_display)

    st.subheader("Property Data List")

    if n_excluded > 0:
        st.caption(f"Showing {n_showing} of {n_total} parcels — {n_excluded} excluded")

    st.write("💡 *Select rows with the checkboxes to export or exclude records. Click a row to center the map on that parcel.*")

    display_cols = [
        "parcelnumb", "alt_parcelnumb1",
        "full_mail_address", "address", "county", "state2", "szip",
        "owner",
        "variance_acres", "variance_pct_display", "assessor_acres_clean", "ll_gisacre",
        "usedesc", "zoning", "saleprice", "lbcs_function_desc", "lbcs_structure_desc"
    ]

    # Capture the selection event (multi-row for checkboxes)
    selection_event = st.dataframe(
        gdf_display[display_cols],
        use_container_width=True,
        height=400,
        on_select="rerun",
        selection_mode="multi-row",
        key="data_table"
    )

    # Selected row indices (for export, map, and exclusion)
    selected_indices = []
    if selection_event and selection_event.selection.rows:
        selected_indices = list(selection_event.selection.rows)

    # --- 5. LOGOUT, EXPORT & EXCLUDE ---
    st.divider()
    col1, col2, col3, col4 = st.columns([1, 2, 2, 2])
    with col1:
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
    with col2:
        csv = gdf_display.drop(columns='geometry').to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Lead List (CSV)", data=csv, file_name="leads.csv", mime="text/csv")
    with col3:
        selected_csv = b""
        if selected_indices:
            selected_gdf = gdf_display.iloc[selected_indices].drop(columns="geometry")
            selected_csv = selected_gdf.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Export selected rows (CSV)",
            data=selected_csv,
            file_name="leads_selected.csv",
            mime="text/csv",
            disabled=len(selected_indices) == 0,
            help="Select rows with the checkboxes above, then click to download only those records."
        )
    with col4:
        if st.button(
            "🚫 Exclude selected",
            disabled=len(selected_indices) == 0,
            help="Remove selected rows from your working dataset. Exclusions are saved and persist across sessions."
        ):
            do_exclude_parcels(gdf_display, selected_indices)
            st.rerun()

    if not selected_indices:
        st.caption("Select one or more rows with the checkboxes to enable « Export selected rows (CSV) » and « Exclude selected ».")

    # --- 6. EXCLUDED PARCELS PANEL ---
    exclusions = st.session_state["exclusions"]
    all_parcelnumbs = set(gdf["parcelnumb"])
    # Only show exclusions whose parcelnum still exists in the current dataset
    active_exclusions = [e for e in exclusions if e["parcelnumb"] in all_parcelnumbs]

    if active_exclusions:
        with st.expander(f"Excluded parcels ({len(active_exclusions)})"):
            if st.button("Restore all", key="restore_all"):
                do_restore_all()
                st.rerun()
            st.divider()
            for exc in active_exclusions:
                c1, c2, c3, c4, c5 = st.columns([2, 3, 3, 2, 1])
                with c1:
                    st.text(exc["parcelnumb"])
                with c2:
                    st.text(exc.get("owner", ""))
                with c3:
                    st.text(exc.get("address", ""))
                with c4:
                    st.text(exc.get("excluded_at", ""))
                with c5:
                    if st.button("Restore", key=f"restore_{exc['parcelnumb']}"):
                        do_restore_parcel(exc["parcelnumb"])
                        st.rerun()


# --- ENTRY POINT ---
st.set_page_config(layout="wide", page_title="Land Variance Explorer")

if check_password():
    main_app()
