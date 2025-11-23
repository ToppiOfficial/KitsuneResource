import os
import sys
import json
import argparse
import win32com.client

PRESET_CONFIGS = {
    "l4d2": [
        "Bill", "Francis", "Louis", "Zoey",
        "Coach", "Ellis", "Nick", "Rochelle",
        "Shared Assets"
    ]
}

PsTextLayer = 2
PsDoNotSaveChanges = 2
PsDisplayNoDialogs = 3
PsDisplayAllDialogs = 1

def find_text_layer(layers, target_name):
    """
    Recursive search for a text layer by name.
    Returns the layer object or None.
    """
    for layer in layers:
        # Check if it is a text layer with the correct name
        if getattr(layer, "Kind", None) == PsTextLayer and layer.Name == target_name:
            return layer
        
        # If it's a group/set, search inside it recursively
        if hasattr(layer, "Layers"):
            found = find_text_layer(layer.Layers, target_name)
            if found:
                return found
    return None

def setup_jpeg_options():
    """Configures and returns the JPEG save options object."""
    jpeg_options = win32com.client.Dispatch("Photoshop.JPEGSaveOptions")
    jpeg_options.Quality = 9
    return jpeg_options

def process_psd(psApp, psd_path, text_layer_name, characters, output_dir):
    """Open a PSD, update the text layer, and export JPEGs."""
    
    # Open the document
    try:
        doc = psApp.Open(psd_path)
    except Exception as e:
        print(f"[!] Failed to open {psd_path}: {e}")
        return

    try:
        # Locate the layer
        layer_to_edit = find_text_layer(doc.Layers, text_layer_name)
        
        if not layer_to_edit:
            print(f"[!] Text layer '{text_layer_name}' not found in {os.path.basename(psd_path)}")
            return # Finally block will handle closing

        original_text = layer_to_edit.TextItem.Contents
        
        jpeg_options = setup_jpeg_options()

        print(f"[*] Exporting {len(characters)} variations...")

        for char in characters:
            # Update text (preserving your original formatting)
            layer_to_edit.TextItem.Contents = f"({char})"
            
            # Create safe filename
            safe_name = char.replace(" ", "_")
            export_path = os.path.join(output_dir, f"{safe_name}.jpg")

            # Save as Copy
            doc.SaveAs(export_path, jpeg_options, True)
            print(f"    -> {safe_name}.jpg")

        layer_to_edit.TextItem.Contents = original_text

    except Exception as e:
        print(f"[!] Error processing {os.path.basename(psd_path)}: {e}")
    
    finally:
        # Always close the document, even if errors occurred
        doc.Close(PsDoNotSaveChanges)

def load_characters(source):
    """Load characters from a JSON file or a preset key."""
    key = source.lower()
    
    if key in PRESET_CONFIGS:
        return PRESET_CONFIGS[key]

    if not os.path.exists(source):
        print(f"[!] Error: '{source}' is not a known preset or a valid file path.")
        sys.exit(1)

    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("JSON root must be a list.")
            return data
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[!] Invalid JSON file: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Export PSD text layer variations to JPEG.")
    parser.add_argument("-e", "--entry", required=True, help="PSD file or folder containing PSDs")
    parser.add_argument("layername", help="Name of the text layer to modify")
    parser.add_argument("characters_json", help="JSON file path or preset name (e.g. 'l4d2')")
    parser.add_argument("output", nargs="?", default=None, help="Output folder (optional)")

    args = parser.parse_args()

    entry_path = os.path.abspath(args.entry)
    output_dir_root = os.path.abspath(args.output) if args.output else None
    
    characters = load_characters(args.characters_json)

    # Initialize Photoshop
    try:
        psApp = win32com.client.Dispatch("Photoshop.Application")
        # Suppress dialogs (e.g., missing fonts) to prevent hanging
        previous_interaction_level = psApp.DisplayDialogs
        psApp.DisplayDialogs = PsDisplayNoDialogs
        psApp.Visible = True
    except Exception as e:
        print(f"[!] Failed to connect to Photoshop: {e}")
        sys.exit(1)

    # Collect files
    psd_files = []
    if os.path.isdir(entry_path):
        psd_files = [
            os.path.join(entry_path, f) 
            for f in os.listdir(entry_path) 
            if f.lower().endswith(".psd")
        ]
    elif os.path.isfile(entry_path) and entry_path.lower().endswith(".psd"):
        psd_files = [entry_path]
    else:
        print("[!] Entry must be a PSD file or a folder containing PSDs.")
        sys.exit(1)

    if not psd_files:
        print("[!] No PSD files found in target location.")
        sys.exit(0)

    try:
        for psd in psd_files:
            psd_name = os.path.splitext(os.path.basename(psd))[0]
            
            if output_dir_root:
                target_dir = output_dir_root
            else:
                # Default /entry_folder/psd_name/
                target_dir = os.path.join(os.path.dirname(psd), psd_name)
            
            os.makedirs(target_dir, exist_ok=True)
            
            print(f"--- Processing: {psd_name} ---")
            process_psd(psApp, psd, args.layername, characters, target_dir)
            print("--- Done ---\n")
            
    finally:
        # Restore user's dialog settings
        psApp.DisplayDialogs = previous_interaction_level

if __name__ == "__main__":
    main()