import os
import glob
import json

def compile_search_index(archive_dir="smbc_archive"):
    meta_dir = os.path.join(archive_dir, "metadata")
    output_file = os.path.join(archive_dir, "search_index.json")
    
    json_files = glob.glob(os.path.join(meta_dir, "*.json"))
    compact_records = []
    
    print(f"Compiling snippet search index from {len(json_files)} profiles...")

    for json_path in json_files:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Keep structural transcript elements separate for context mapping
            main_panels = data.get("transcribed_text", "")
            votey_panels = data.get("votey_text", "")
            hover_text = data.get("hover_text", "")
            bsky_alt = data.get("main_comic_alt_text", "")
            
            # Combine all pieces into an array of searchable sentences/lines
            all_lines = []
            for item in [main_panels, votey_panels, hover_text, bsky_alt]:
                if item and isinstance(item, str):
                    # Basic sanitization to clean up duplicate whitespace
                    cleaned = " ".join(item.split())
                    if cleaned and cleaned not in all_lines:
                        all_lines.append(cleaned)

            compact_records.append({
                "id": data.get("comic_id"),
                "title": data.get("title", "Untitled"),
                "date": data.get("date", "Unknown"),
                "lines": all_lines  # Array of strings for localized snippet matching
            })
        except Exception:
            continue

    # Chronological sort: Newest comics first
    compact_records.sort(key=lambda x: x["date"], reverse=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(compact_records, f, ensure_ascii=False)
        
    print(f"Generated text search index: {output_file}")

if __name__ == "__main__":
    compile_search_index()