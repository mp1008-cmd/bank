import os
import re
from collections import defaultdict 
from flask import Flask, request, render_template, redirect, url_for, send_file, flash
import pandas as pd

# Flask app initialization
app = Flask(__name__)
app.secret_key = "supersecretkey"

# Directories for file uploads
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

# Utility function to detect a column based on keywords
def detect_column(df, keywords):
    """Detect column by scanning all columns for keyword matches."""
    for col in df.columns:
        if any(keyword.lower() in col.lower() for keyword in keywords):
            return col
    return None




# Route: Home Page
@app.route("/", methods=["GET", "POST"])
def home():
    return render_template("index.html")

# Route: Frequency Analysis
@app.route("/frequency", methods=["POST"])
def frequency_analysis():
    files = request.files.getlist("files")
    print(f"Files received: {[file.filename for file in files]}")  # Debugging

    if not files or all(file.filename == "" for file in files):
        flash("Please upload files for frequency analysis.", "error")
        return redirect(url_for("home"))

    combined_data = []  # List to store data from all files
    result_files = []   # To store result filenames

    for file in files:
        if file.filename == "":
            flash("One of the files is empty. Skipping it.", "error")
            continue

        try:
            # Save file temporarily
            temp_file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(temp_file_path)

            # Load the file using pandas
            df = pd.read_excel(temp_file_path, engine="openpyxl")

            # Detect the description column dynamically
            desc_col = detect_column(df, ["description", "txn_desc", "narration", "particulars", "transaction details", "remarks"])
            if not desc_col:
                flash(f"Description column not found in {file.filename}.", "error")
                continue

            # Detect credit, debit, and amount columns dynamically
            credit_col = detect_column(df, ["credit", "deposit", "cr", "credit amount"])
            debit_col = detect_column(df, ["debit", "withdrawal", "dr", "debit amount"])
            amount_col = detect_column(df, ["amount", "transaction amount", "transaction_amount"])

            # Standardize the description column for consistency
            df[desc_col] = df[desc_col].astype(str).str.strip()

            # Extract mobile numbers or UPI IDs before '@'
            df['Extracted Mobile/UPI'] = df[desc_col].str.extract(r'([0-9]{10}@[a-z]+|[0-9]{10})', expand=False)

            # Extract names for NEFT/IMPS transactions
            df['Extracted Names'] = df[desc_col].str.extract(
                r'(?:NEFT|IMPS|RTGS).*?-([A-Za-z]+[A-Za-z ]+)-[A-Z0-9]{11}', expand=False
            )

            # Combine extracted numbers and names for grouping
            df['Combined Key'] = df['Extracted Names'].combine_first(df['Extracted Mobile/UPI'])

            # Add detection for payment methods (IMPS, NEFT, UPI, etc.)
            payment_methods = ["UPI", "IMPS", "NEFT", "RTGS", "PAYTM", "BHIM", "PHONEPE"]
            for method in payment_methods:
                df[method] = df[desc_col].str.contains(method, case=False, na=False)

            # Drop rows with no combined key
            df = df.dropna(subset=['Combined Key'])

            # Add the filename to identify the origin of data
            df["Source File"] = file.filename

            # Append relevant data to combined_data
            combined_data.append(df[["Combined Key", "Source File"]])

        except Exception as e:
            flash(f"Error processing {file.filename}: {e}", "error")
            continue

    # Combine all data across files
    if combined_data:
        combined_df = pd.concat(combined_data, ignore_index=True)

        # Count occurrences of each key across all files
        frequency_counts = combined_df["Combined Key"].value_counts()

        # Identify keys that appear in multiple files
        common_keys = frequency_counts[frequency_counts > 1]
        if not common_keys.empty:
            # Create a DataFrame for keys found in multiple files
            common_data = combined_df[combined_df["Combined Key"].isin(common_keys.index)].copy()
            common_data["Frequency"] = common_data["Combined Key"].map(frequency_counts)

            # Identify if there’s exactly one common key across all files
            if len(common_keys) == 1:
                single_common_key = common_keys.index[0]
                flash(f"A single common key was found across files: {single_common_key}", "success")
            else:
                flash(f"Multiple common keys were found across files.", "info")

            # Save the results to an output file
            output_filename = "common_keys_analysis.xlsx"
            output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_filename)
            common_data.to_excel(output_path, index=False, engine="openpyxl")
            result_files.append(output_filename)
        else:
            flash("No common keys found across the uploaded files.", "info")
    else:
        flash("No valid data found in the uploaded files.", "error")

    # Render results
    if result_files:
        return render_template("result.html", result_files=result_files)
    else:
        return redirect(url_for("home"))



###
# Route: Range Analysis (Greater Than / Lesser Than for Credit, Debit, or Both)
@app.route("/range_analysis", methods=["POST"])
def range_analysis():
    files = request.files.getlist("files")
    greater_than = request.form.get("greater_than", type=float)
    less_than = request.form.get("less_than", type=float)
    transaction_type = request.form.get("transaction_type")  # "credit", "debit", or "both"

    if not files or (greater_than is None and less_than is None):
        flash("Please upload files and enter a valid range.", "error")
        return redirect(url_for("home"))

    result_files = []
    for file in files:
        try:
            temp_file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(temp_file_path)

            # Load the file
            df = pd.read_excel(temp_file_path, engine="openpyxl")

            # Detect credit and debit columns dynamically
            credit_col = detect_column(df, ["credit", "deposit", "cr", "credit amount"])
            debit_col = detect_column(df, ["debit", "withdrawal", "dr", "debit amount"])

            if not credit_col and not debit_col:
                flash(f"Credit or Debit columns not found in {file.filename}.", "error")
                continue

            # Convert detected columns to numeric for filtering
            if credit_col:
                df[credit_col] = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
            if debit_col:
                df[debit_col] = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)

            # Apply filtering conditions based on user selection
            filtered_df = df.copy()
            if transaction_type == "credit" and credit_col:
                filtered_df = df[(df[credit_col] > greater_than) & (df[credit_col] < less_than)]
            elif transaction_type == "debit" and debit_col:
                filtered_df = df[(df[debit_col] > greater_than) & (df[debit_col] < less_than)]
            elif transaction_type == "both":
                # Handle case where one of the columns is missing
                if credit_col and debit_col:
                    filtered_df = df[((df[credit_col] > greater_than) & (df[credit_col] < less_than)) | 
                                     ((df[debit_col] > greater_than) & (df[debit_col] < less_than))]
                elif credit_col:
                    filtered_df = df[(df[credit_col] > greater_than) & (df[credit_col] < less_than)]
                elif debit_col:
                    filtered_df = df[(df[debit_col] > greater_than) & (df[debit_col] < less_than)]
            
            if filtered_df.empty:
                flash(f"No transactions found within the range in {file.filename}.", "info")
                continue

            # Save the filtered transactions
            output_filename = f"{os.path.splitext(file.filename)[0]}_range_filtered.xlsx"
            output_path = os.path.join(UPLOAD_FOLDER, output_filename)
            filtered_df.to_excel(output_path, index=False, engine="openpyxl")

            result_files.append(output_filename)
        except Exception as e:
            flash(f"Error processing {file.filename}: {e}", "error")
            continue

    return render_template("result.html", result_files=result_files)





##




# Route: Categorization
@app.route("/categorize", methods=["POST"])
def categorize_transactions():
    files = request.files.getlist("files")
    selected_categories = request.form.getlist("categories")
    if not files:
        flash("Please upload files to categorize.", "error")
        return redirect(url_for("home"))

    result_files = []
    for file in files:
        try:
            temp_file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(temp_file_path)

            # Load file
            df = pd.read_excel(temp_file_path, engine="openpyxl")

            # Detect description-like column
            desc_col = detect_column(df, ["description", "details", "narration", "particulars"])
            if not desc_col:
                flash(f"Description column not found in {file.filename}.", "error")
                continue

            # Define categories
            categories = {
                "UPI": ["upi", "paytm", "google pay", "phonepe"],
                "Card": ["card", "debit card", "credit card"],
                "Withdrawal": ["atm withdrawal", "cash withdrawal"],
                "NEFT": ["neft"],
                "IMPS": ["imps"],
                "RTGS": ["rtgs"],
                "Others": []
            }

            # Assign category based on description
            def assign_category(description):
                if pd.isna(description):
                    return "Others"
                desc = str(description).lower()
                for category, keywords in categories.items():
                    if category in selected_categories and any(keyword in desc for keyword in keywords):
                        return category
                return "Others"

            df["Category"] = df[desc_col].apply(assign_category)

            # Filter data based on the selected categories
            filtered_df = df[df["Category"].isin(selected_categories)]

            # Save result to file
            output_filename = f"{os.path.splitext(file.filename)[0]}_categorized.xlsx"
            output_path = os.path.join(UPLOAD_FOLDER, output_filename)
            filtered_df.to_excel(output_path, index=False, engine="openpyxl")

            result_files.append(output_filename)
        except Exception as e:
            flash(f"Error processing {file.filename}: {e}", "error")
            continue

    return render_template("result.html", result_files=result_files)

# Route: Total Deposits and Withdrawals
@app.route("/calculate_totals", methods=["POST"])
def calculate_totals():
    files = request.files.getlist("files")
    if not files:
        flash("Please upload files for calculating totals.", "error")
        return redirect(url_for("home"))

    result_files = []
    for file in files:
        try:
            temp_file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(temp_file_path)


            # Load file
            df = pd.read_excel(temp_file_path, engine="openpyxl")

            # Detect columns for deposits and withdrawals
            credit_col = detect_column(df, ["credit", "deposits", "amount", "deposit"])
            debit_col = detect_column(df, ["debit", "withdrawals", "amount", "withdrawal"])

            if not credit_col and not debit_col:
                flash(f"Credit or debit columns not found in {file.filename}.", "error")
                continue

            # Ensure numeric conversion for calculations
            if credit_col:
                df[credit_col] = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
                total_credit = df[credit_col].sum()
            else:
                total_credit = 0

            if debit_col:
                df[debit_col] = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
                total_debit = df[debit_col].sum()
            else:
                total_debit = 0

            # Save result to a new Excel file
            totals_df = pd.DataFrame({
                "File Name": [file.filename],
                "Total Deposited (Credit)": [total_credit],
                "Total Withdrawn (Debit)": [total_debit],
            })
            output_filename = f"{os.path.splitext(file.filename)[0]}_totals.xlsx"
            output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_filename)
            totals_df.to_excel(output_path, index=False, engine="openpyxl")

            result_files.append(output_filename)
        except Exception as e:
            flash(f"Error processing {file.filename}: {e}", "error")
            continue

    return render_template("result.html", result_files=result_files)


# -------------------------------
# Helper Functions for Transaction Analysis
# -------------------------------

import os
import pandas as pd
from flask import Flask, request, send_from_directory, flash, redirect, url_for
from collections import defaultdict
import re

app.config["UPLOAD_FOLDER"] = "uploads"  # Folder where files are temporarily saved
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

TRANSACTION_TYPES = ["UPI", "Card", "Cash Withdrawal", "NEFT", "IMPS", "RTGS"]

def extract_names_and_upi(text):
    """Extract UPI IDs, Names, and Transaction Types from transaction descriptions."""
    upi_pattern = r'(\d{10}@[a-zA-Z]+|[a-zA-Z]+@[a-zA-Z]+)'  # UPI ID pattern
    name_pattern = r'\b[A-Z][a-z]+\s[A-Z][a-z]+\b'  # Name pattern (First Last name)
    
    transaction_type = None
    for ttype in TRANSACTION_TYPES:
        if ttype.lower() in text.lower():  # Detect if transaction type is in the description
            transaction_type = ttype
            break
    
    upi_matches = re.findall(upi_pattern, text)
    name_matches = re.findall(name_pattern, text)

    return transaction_type, set(upi_matches + name_matches)

@app.route("/common_names", methods=["POST"])
def common_names():
    files = request.files.getlist("files")
    if not files:
        flash("Please upload bank statements to find common names.", "error")
        return redirect(url_for("home"))

    name_counts = defaultdict(int)
    transaction_details = defaultdict(list)
    full_transaction_data = []  # This will store the full transaction details for the additional sheet
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    try:
        print(f"\n📂 Processing {len(files)} files...")

        for file in files:
            temp_file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(temp_file_path)
            print(f"✔ File saved: {file.filename}")

            df = pd.read_excel(temp_file_path, engine="openpyxl")
            print(f"✔ Loaded Excel file: {file.filename}, Columns: {list(df.columns)}")

            description_columns = ["description", "narration", "particulars", "transaction details", "remarks"]
            desc_col = next((col for col in df.columns if col.lower() in description_columns), None)

            if not desc_col:
                print(f"❌ No description column found in {file.filename}. Available columns: {list(df.columns)}")
                flash(f"No description column found in {file.filename}.", "warning")
                continue

            print(f"✔ Found Description Column: {desc_col}")

            amount_columns = ['amount', 'deposits', 'withdrawals', 'dr / cr', 'balance']
            amount_col = next((col for col in df.columns if col.lower() in amount_columns), None)

            if not amount_col:
                print(f"❌ No amount column found in {file.filename}.")
                flash(f"No amount column found in {file.filename}.", "warning")
                continue

            print(f"✔ Found Amount Column: {amount_col}")

            for _, row in df.iterrows():
                text = str(row[desc_col])  # Ensure it's a string
                amount = row[amount_col]

                # Convert amount to numeric, coercing errors to NaN
                amount = pd.to_numeric(amount, errors='coerce')
                print(f"Processing transaction: {text}, Amount: {amount}")

                if pd.isna(amount) or amount == 0:
                    print(f"❌ Skipping row with no valid amount in {file.filename}")
                    continue

                transaction_type, extracted_data = extract_names_and_upi(text)
                print(f"Extracted Data: {extracted_data}, Transaction Type: {transaction_type}")

                if extracted_data:
                    for item in extracted_data:
                        name_counts[item] += 1
                        transaction_details[item].append({
                            "Amount": amount,
                            "Description": text,
                            "Transaction Type": transaction_type,
                            "File": file.filename
                        })

                        full_transaction_data.append({
                            "Name": item,
                            "Transaction Type": transaction_type,
                            "Amount": amount,
                            "Description": text,
                            "File": file.filename
                        })
                else:
                    print(f"❌ No valid data extracted for: {text}")

        common_names_list = {name: count for name, count in name_counts.items() if count > 1}
        print(f"Common Names List: {common_names_list}")

        if not common_names_list:
            print("⚠ No common names found, returning empty file.")
            output_filename = "common_names_empty.xlsx"
            output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_filename)
            pd.DataFrame(columns=["Common Name", "Frequency"]).to_excel(output_path, index=False, engine="openpyxl")
        else:
            output_filename = "common_names.xlsx"
            common_df = pd.DataFrame(list(common_names_list.items()), columns=["Common Name", "Frequency"])

            transaction_data = []
            for name, transactions in transaction_details.items():
                total_amount = sum([txn['Amount'] for txn in transactions if isinstance(txn['Amount'], (int, float))])
                total_transactions = len(transactions)
                transaction_types = set(txn['Transaction Type'] for txn in transactions)
                
                transaction_data.append({
                    "Name": name,
                    "Total Transactions": total_transactions,
                    "Total Amount": total_amount,
                    "Transaction Types": ", ".join([str(ttype) for ttype in transaction_types if ttype])
                })
            transaction_df = pd.DataFrame(transaction_data)

            full_transaction_df = pd.DataFrame(full_transaction_data)

            output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_filename)
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                common_df.to_excel(writer, sheet_name="Common Names", index=False)
                transaction_df.to_excel(writer, sheet_name="Transaction Details", index=False)
                full_transaction_df.to_excel(writer, sheet_name="Full Transaction Details", index=False)

        print(f"File ready for download: {output_filename}")

        # Send the file for download
        return send_from_directory(app.config["UPLOAD_FOLDER"], output_filename, as_attachment=True)

    except Exception as e:
        print(f"❌ Error: {e}")
        flash(f"Error processing files: {e}", "error")

    return redirect(url_for("home"))


# Route: Download Processed Files
@app.route("/download/<path:filename>")
def download_file(filename):
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    return send_file(file_path, as_attachment=True)

# Run the app
if __name__ == "__main__":
    app.run(debug=True)
