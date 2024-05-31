import streamlit as st
import pandas as pd
from openpyxl import Workbook
import json
import datetime

# Page configuration
st.set_page_config(page_title="Leads Management Portal", page_icon=":page_with_curl:", layout="wide")

# Styles
st.markdown(
    """
    <style>
    .sidebar .sidebar-content { background-color: #f0f2f6; }
    .stButton>button { background-color: #4CAF50; color: white; }
    .stButton>button:hover { background-color: #45a049; }
    .stForm { background-color: #f9f9f9; padding: 20px; border-radius: 10px; }
    .stTextInput>div>div>input { border-radius: 5px; }
    </style>
    """,
    unsafe_allow_html=True
)

# Constants
BUSINESS_TYPES = ["Manufacturer", "Distributor", "Wholesaler", "Retailer", "Service Provider"]
CALL_STATUSES = ["Pending", "In Progress", "Completed", "Failed"]

EXCEL_FILE = "Database.xlsx"
USER_FILE = "users.json"
SHEET_NAME = "Leads"
COLUMNS = [
    "Customer Name", "Mobile number", "Business Name", "Business type", "GOV", "City", 
    "Lead Source", "Call status", "Tax registered (electronic invoices)", "Feedback", 
    "Disqualified reason", "Comment", "Assigned Agent", "Date"
]

# Load or initialize user data
def load_user_data():
    try:
        with open(USER_FILE, "r") as file:
            users = json.load(file)
            for user in users:
                if "active" not in users[user]:
                    users[user]["active"] = True
            return users
    except (FileNotFoundError, json.JSONDecodeError):
        return {"admin": {"password": "admin", "role": "admin", "active": True}}

def save_user_data(users):
    with open(USER_FILE, "w") as file:
        json.dump(users, file)

# Read Excel file
def read_excel(file_path, sheet_name, columns):
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        df = df.dropna(how="all")
        for column in columns:
            if column not in df.columns:
                df[column] = ""
    except FileNotFoundError:
        df = pd.DataFrame(columns=columns)
    except ValueError as e:
        if "Worksheet named" in str(e):
            wb = Workbook()
            ws = wb.active
            ws.title = sheet_name
            wb.save(file_path)
            df = pd.DataFrame(columns=columns)
        else:
            raise e
    return df

def update_excel(file_path, sheet_name, df):
    with pd.ExcelWriter(file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)

def authenticate(username, password, users):
    if username in users and users[username]["password"] == password and users[username]["active"]:
        return True
    return False

def get_filtered_data(data, status):
    return data[data["Call status"] != status]

def login_ui():
    st.sidebar.header("Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login", key="login_button"):
        if authenticate(username, password, users):
            st.session_state.logged_in = True
            st.session_state.username = username
            st.experimental_set_query_params(view="dashboard")
        else:
            st.warning("Incorrect username or password")

def logout_ui():
    st.sidebar.header(f"Welcome, {st.session_state.username}")
    if st.sidebar.button("Logout", key="logout_button"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.experimental_set_query_params(view="login")

def navigation_ui(role):
    actions = {
        "Update Lead Status": "update",
        "View All Leads": "view_all",
        "Delete Lead": "delete",
        "Add User": "add_user",
        "Manage Users": "manage_users",
        "Assign Leads": "assign_leads",
        "View Performance": "view_performance",
        "My Leads": "my_leads"
    }
    
    if role == "agent":
        actions = {key: value for key, value in actions.items() if value in ["update", "my_leads"]}
    
    for label, action in actions.items():
        if st.sidebar.button(label, key=f"nav_{action}"):
            st.experimental_set_query_params(view=action)

def dashboard_ui():
    st.markdown("### Dashboard")
    st.markdown("Welcome to the Leads Management Portal!")

def onboard_lead_ui(existing_data):
    st.markdown("### Onboard New Lead")
    st.markdown("Enter the details of the new Lead below.")
    with st.form(key="lead_form", clear_on_submit=True):
        customer_name = st.text_input("Customer Name*", placeholder="Enter customer name")
        mobile_number = st.text_input("Mobile Number*", placeholder="Enter mobile number")
        business_name = st.text_input("Business Name*", placeholder="Enter business name")
        business_type = st.selectbox("Business Type*", options=BUSINESS_TYPES, index=0)
        gov = st.text_input("GOV", placeholder="Enter GOV")
        city = st.text_input("City", placeholder="Enter city")
        lead_source = st.text_input("Lead Source", placeholder="Enter lead source")
        call_status = st.selectbox("Call Status", options=CALL_STATUSES, index=0)
        tax_registered = st.selectbox("Tax Registered (electronic invoices)", options=["Yes", "No"], index=1)
        feedback = st.text_area("Feedback", placeholder="Enter feedback")
        disqualified_reason = st.text_area("Disqualified Reason", placeholder="Enter disqualified reason")
        comment = st.text_area("Comment", placeholder="Enter comment")
        
        st.markdown("**required*")
        submit_button = st.form_submit_button(label="Submit Lead Details")
        if submit_button:
            if not customer_name or not mobile_number or not business_name or not business_type:
                st.warning("Ensure all mandatory fields are filled.")
            elif len(mobile_number)!= 11 or not mobile_number.isdigit():
                st.warning("Mobile number should be an 11-digit number.")
            else:
                new_lead = {
                    "Customer Name": customer_name,
                    "Mobile number": mobile_number,
                    "Business Name": business_name,
                    "Business type": business_type,
                    "GOV": gov,
                    "City": city,
                    "Lead Source": lead_source,
                    "Call status": call_status,
                    "Tax registered (electronic invoices)": tax_registered,
                    "Feedback": feedback,
                    "Disqualified reason": disqualified_reason,
                    "Comment": comment,
                    "Assigned Agent": "",
                    "Date": datetime.datetime.now().strftime("%Y-%m-%d")
                }
                existing_data = existing_data.append(new_lead, ignore_index=True)
                update_excel(EXCEL_FILE, SHEET_NAME, existing_data)
                st.success("New lead details submitted successfully!")

def update_lead_ui(existing_data, display_data):
    st.markdown("### Update Lead Status")
    agent_leads = display_data[display_data["Assigned Agent"] == st.session_state.username]
    lead_names = agent_leads["Customer Name"].unique()
    selected_lead = st.selectbox("Select Lead", lead_names)
    if selected_lead:
        lead_index = agent_leads[agent_leads["Customer Name"] == selected_lead].index[0]
        lead_data = agent_leads.loc[lead_index]
        with st.form(key="update_form", clear_on_submit=True):
            new_call_status = st.selectbox(
                "Call Status",
                options=CALL_STATUSES,
                index=CALL_STATUSES.index(lead_data["Call status"]) if lead_data["Call status"] in CALL_STATUSES else 0
            )
            new_feedback = st.text_area("Feedback", value=lead_data["Feedback"])
            new_comment = st.text_area("Comment", value=lead_data["Comment"])
            new_business_name = st.text_input("Business Name", value=lead_data["Business Name"])
            new_business_type = st.selectbox(
                "Business type",
                options=BUSINESS_TYPES,
                index=BUSINESS_TYPES.index(lead_data["Business type"]) if lead_data["Business type"] in BUSINESS_TYPES else 0
            )
            new_gov = st.text_input("GOV", value=lead_data["GOV"])
            new_city = st.text_input("City", value=lead_data["City"])
            new_lead_source = st.text_input("Lead Source", value=lead_data["Lead Source"])
            new_tax_registered = st.selectbox(
                "Tax registered (electronic invoices)",
                options=["Yes", "No"],
                index=0 if lead_data["Tax registered (electronic invoices)"] == "Yes" else 1
            )
            new_disqualified_reason = st.text_area("Disqualified reason", value=lead_data["Disqualified reason"])
            new_update_button = st.form_submit_button("Update Lead")
            if new_update_button:
                existing_data.at[lead_index, "Call status"] = new_call_status
                existing_data.at[lead_index, "Feedback"] = new_feedback
                existing_data.at[lead_index, "Comment"] = new_comment
                existing_data.at[lead_index, "Business Name"] = new_business_name
                existing_data.at[lead_index, "Business type"] = new_business_type
                existing_data.at[lead_index, "GOV"] = new_gov
                existing_data.at[lead_index, "City"] = new_city
                existing_data.at[lead_index, "Lead Source"] = new_lead_source
                existing_data.at[lead_index, "Tax registered (electronic invoices)"] = new_tax_registered
                existing_data.at[lead_index, "Disqualified reason"] = new_disqualified_reason
                update_excel(EXCEL_FILE, SHEET_NAME, existing_data)
                st.success("Lead status updated successfully!")
                
                # Remove updated lead from the list
                display_data = existing_data[existing_data["Call status"]!= "Completed"]
                agent_leads = display_data[display_data["Assigned Agent"] == st.session_state.username]
                lead_names = agent_leads["Customer Name"].unique()
                selected_lead = st.selectbox("Select Lead", lead_names)

def view_all_leads_ui(display_data):
    st.markdown("### View All Leads")
    st.dataframe(display_data)

def delete_lead_ui(existing_data, display_data):
    st.markdown("### Delete Lead")
    lead_names = display_data["Customer Name"].unique()
    selected_lead = st.selectbox("Select Lead to Delete", lead_names)
    if selected_lead:
        lead_index = display_data[display_data["Customer Name"] == selected_lead].index[0]
        if st.button("Delete Lead"):
            existing_data = existing_data.drop(lead_index)
            update_excel(EXCEL_FILE, SHEET_NAME, existing_data)
            st.success("Lead deleted successfully!")
            display_data = existing_data[existing_data["Call status"]!= "Completed"]

def add_user_ui(users):
    st.markdown("### Add New User")
    with st.form(key="add_user_form", clear_on_submit=True):
        username = st.text_input("Username*", placeholder="Enter username")
        password = st.text_input("Password*", type="password", placeholder="Enter password")
        role = st.selectbox("Role*", options=["admin", "agent", "team leader"])
        active = st.checkbox("Active*", value=True)
        submit_button = st.form_submit_button(label="Add User")
        
        if submit_button:
            if not username:
                st.error("Username is required.")
            elif not password:
                st.error("Password is required.")
            elif not role:
                st.error("Role is required.")
            elif username in users:
                st.warning("Username already exists.")
            else:
                users[username] = {"password": password, "role": role, "active": active}
                save_user_data(users)
                st.success("User added successfully!")

def manage_users_ui(users):
    st.markdown("### Manage Users")
    username = st.selectbox("Select User to Manage", options=list(users.keys()))
    if username:
        with st.form(key="manage_user_form", clear_on_submit=True):
            password = st.text_input("Password*", value=users[username]["password"], type="password")
            role = st.selectbox("Role*", options=["admin", "agent"], index=["admin", "agent"].index(users[username]["role"]))
            active = st.checkbox("Active*", value=users[username]["active"])
            submit_button = st.form_submit_button(label="Update User")
            if submit_button:
                users[username] = {"password": password, "role": role, "active": active}
                save_user_data(users)
                st.success("User details updated successfully!")

def assign_leads_ui(existing_data, display_data, users):
    st.markdown("### Assign Leads")
    agents = [user for user, data in users.items() if data["role"] == "agent" and data["active"]]
    leads_to_assign = display_data[display_data["Assigned Agent"].isnull() | (display_data["Assigned Agent"] == "")]
    if not leads_to_assign.empty:
        with st.form(key="assign_form", clear_on_submit=True):
            selected_leads = st.multiselect(
                "Select Leads to Assign",
                options=[f"{row['Customer Name']} - {row['Mobile number']} ({row['Business Name']})" for _, row in leads_to_assign.iterrows()],
                key="leads_multiselect"
            )
            selected_agent = st.selectbox("Assign Selected Leads to Agent", [""] + agents, key="agent_select")
            submit_button = st.form_submit_button(label="Assign Leads")
            
            if submit_button and selected_leads and selected_agent:
                for lead_info in selected_leads:
                    lead_customer_name = lead_info.split(" - ")[0]
                    lead_index = leads_to_assign[leads_to_assign["Customer Name"] == lead_customer_name].index[0]
                    existing_data.at[lead_index, "Assigned Agent"] = selected_agent
                update_excel(EXCEL_FILE, SHEET_NAME, existing_data)
                st.success("Selected leads assigned to agent successfully!")
                # Update the display data
                display_data = existing_data[existing_data["Call status"] != "Completed"]
            elif not selected_leads:
                st.warning("Please select at least one lead to assign.")
            elif not selected_agent:
                st.warning("Please select an agent to assign the leads.")
    else:
        st.info("No unassigned leads available.")


def view_performance_ui(existing_data, users):
    st.markdown("### View Performance")
    
    if users[st.session_state.username]["role"] == "agent":
        agent_name = st.session_state.username
        agent_data = existing_data[existing_data["Assigned Agent"] == agent_name]

        st.markdown("#### Filter by Date Range")
        start_date = st.date_input("Start Date", value=datetime.date.today() - datetime.timedelta(days=30))
        end_date = st.date_input("End Date", value=datetime.date.today())
        
        total_leads = agent_data.shape[0]
        completed_leads = agent_data[agent_data["Call status"] == "Completed"].shape[0]
        remaining_leads = total_leads - completed_leads
        performance_percentage = (completed_leads / total_leads) * 100 if total_leads > 0 else 0

        st.markdown(f"#### Performance Summary for {agent_name}")
        st.markdown(f"**Total Leads:** {total_leads}")
        st.markdown(f"**Completed Leads:** {completed_leads}")
        st.markdown(f"**Remaining Leads:** {remaining_leads}")
        st.markdown(f"**Performance Percentage:** {performance_percentage:.2f}%")

        st.markdown("#### Call Status Breakdown")
        call_status_counts = agent_data["Call status"].value_counts().to_dict()
        for status, count in call_status_counts.items():
            st.markdown(f"**{status}:** {count} leads")

    elif users[st.session_state.username]["role"] == "team_leader":
        agent_performance = []
        agents = [user for user, data in users.items() if data["role"] == "agent" and data["active"]]

        for agent in agents:
            agent_data = existing_data[existing_data["Assigned Agent"] == agent]
            total_leads = agent_data.shape[0]
            completed_leads = agent_data[agent_data["Call status"] == "Completed"].shape[0]
            remaining_leads = total_leads - completed_leads
            performance_percentage = (completed_leads / total_leads) * 100 if total_leads > 0 else 0

            agent_performance.append({
                "Agent": agent,
                "Total Leads": total_leads,
                "Completed Leads": completed_leads,
                "Remaining Leads": remaining_leads,
                "Performance Percentage": performance_percentage
            })

        agent_performance = sorted(agent_performance, key=lambda x: x["Performance Percentage"], reverse=True)

        st.markdown("#### Team Performance Summary")
        for agent in agent_performance:
            st.markdown(f"**{agent['Agent']}**")
            st.markdown(f"Total Leads: {agent['Total Leads']}")
            st.markdown(f"Completed Leads: {agent['Completed Leads']}")
            st.markdown(f"Remaining Leads: {agent['Remaining Leads']}")
            st.markdown(f"Performance Percentage: {agent['Performance Percentage']:.2f}%")
            st.markdown("---")

        performance_df = pd.DataFrame(agent_performance)
        st.dataframe(performance_df)

def my_leads_ui(display_data):
    st.markdown("### My Leads")
    agent_leads = display_data[display_data["Assigned Agent"] == st.session_state.username]
    st.dataframe(agent_leads)

# Main execution flow
users = load_user_data()
existing_data = read_excel(EXCEL_FILE, SHEET_NAME, COLUMNS)
existing_data["Customer Name"] = existing_data["Customer Name"].astype(str)
existing_data["Mobile number"] = existing_data["Mobile number"].astype(str)
display_data = get_filtered_data(existing_data, "Completed")

st.sidebar.title("Menu")
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

if not st.session_state.logged_in:
    login_ui()
else:
    logout_ui()
    navigation_ui(users[st.session_state.username]["role"])

query_params = st.experimental_get_query_params()
view = query_params.get("view", ["dashboard"])[0]

if st.session_state.logged_in:
    if view == "dashboard":
        dashboard_ui()
    elif view == "onboard":
        onboard_lead_ui(existing_data)
    elif view == "update":
        update_lead_ui(existing_data, display_data)
    elif view == "view_all":
        view_all_leads_ui(display_data)
    elif view == "delete":
        delete_lead_ui(existing_data, display_data)
    elif view == "add_user":
        add_user_ui(users)
    elif view == "manage_users":
        manage_users_ui(users)
    elif view == "assign_leads":
        assign_leads_ui(existing_data, display_data, users)
    elif view == "view_performance":
        view_performance_ui(existing_data, users)
    elif view == "my_leads":
        my_leads_ui(display_data)
else:
    st.sidebar.markdown("Please login to access the portal.")

st.sidebar.markdown("---")
st.sidebar.markdown("### About")
st.sidebar.info(
    """
    Leads Management Portal enables the management and tracking of customer leads.
    Admin users can manage leads, agents, and view performance metrics.
    Agents can update the status of their assigned leads and onboard new leads.
    """
)
