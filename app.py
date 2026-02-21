import streamlit as st
import geopandas as gpd
import numpy as np
import pandas as pd
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
import matplotlib.colors as colors
import io
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

# --- 3. MAIN APP INTERFACE ---
def main_app():
    st.title("🗺️ Parcel Size Variance Explorer")

    gdf = load_data()
    selected_parcel_id = None

    selection_event = st.session_state.get("data_table")

    if selection_event and len(selection_event.selection.rows) > 0:
        idx = selection_event.selection.rows[0]
        selected_parcel = gdf.iloc[idx]
        selected_parcel_id = selected_parcel["parcelnumb"]

        target_lat = selected_parcel.geometry.centroid.y
        target_lon = selected_parcel.geometry.centroid.x

        st.session_state.map_center = [target_lat, target_lon]
        st.session_state.map_zoom = 15


    gdf["variance_pct_display"] = np.where(
        (gdf["variance_acres"].notnull()) &
        (gdf["variance_acres"] != 0) &
        (gdf["assessor_acres_clean"].notnull()) &
        (gdf["assessor_acres_clean"] != 0),
        gdf["variance_pct"].round(2).astype(str) + "%",
        ""
    )

    gdf["full_mail_address"] = gdf["mailadd"] + ", " + gdf["mail_city"] + ", " + gdf["mail_state2"] + ", " + gdf["mail_zip"]

    # --- Initialize Session State for Map ---
    if 'map_center' not in st.session_state:
        # Calculate initial center
        avg_lat = gdf.geometry.centroid.y.mean()
        avg_lon = gdf.geometry.centroid.x.mean()
        st.session_state.map_center = [avg_lat, avg_lon]
        st.session_state.map_zoom = 13

    # --- Layout: Map ---
    st.subheader("Interactive Map")
    m = folium.Map(
        location=st.session_state.map_center, 
        zoom_start=st.session_state.map_zoom, 
        tiles="OpenStreetMap"
    )

    # 1. Add the Google Tiles to the Map object
    folium.TileLayer(
        tiles=GOOGLE_TILES["Satellite"]["url"],
        attr=GOOGLE_TILES["Satellite"]["attr"],
        overlay=True,
        name="Satellite"
    ).add_to(m)

    # Log normalization
    vmin, vmax = gdf["variance_acres"].min(), gdf["variance_acres"].max()
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


    # 3. Update the GeoJson style function to use the colormap
    folium.GeoJson(
        gdf,
        style_function=style_function,
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "parcelnumb", "alt_parcelnumb1", "full_mail_address", "address", "county", "state2", "szip",
                "owner",
                "variance_acres", "variance_pct_display",
                "assessor_acres_clean", "ll_gisacre",
                "usedesc", "zoning", "saleprice"
            ],
            aliases=[
                "Parcel Number:", "Tax Map #:", "Full Mailing Address:", "Address:", "County:", "State:", "Zip:",
                "Owner:",
                "Variance Acres:", "Variance Percent:",
                "Deeded Acres:", "Calculated Acres:",
                "Used Description:", "Zoning:", "Sale Price:"
            ]
        )
    ).add_to(m)

    st_folium(m, width="100%", height=500, key="main_map")

    # --- Layout: Table ---
    st.subheader("Property Data List")
    st.write("💡 *Select a row below to center the map on that parcel.*")
    display_cols = ["parcelnumb", "alt_parcelnumb1",
    "full_mail_address", "address", "county", "state2", "szip", 
    "owner",
    "variance_acres", "variance_pct_display", "assessor_acres_clean", "ll_gisacre", 
    "usedesc", "zoning", "saleprice"]
    # Capture the selection event
    selection_event = st.dataframe(
        gdf[display_cols],
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="data_table"
    )

    # --- 5. LOGOUT & EXPORT ---
    st.divider()
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("Logout"):
            st.session_state["password_correct"] = False
            st.rerun()
    with col2:
        csv = gdf.drop(columns='geometry').to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Lead List (CSV)", data=csv, file_name="leads.csv", mime="text/csv")

# --- ENTRY POINT ---
st.set_page_config(layout="wide", page_title="Land Variance Explorer")

if check_password():
    main_app()