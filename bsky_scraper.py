import os
import json
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from atproto import Client
from chrome_lens_py import LensAPI

# --- CONFIGURATION ---
BASE_ARCHIVE_DIR = "smbc_archive"
INDEX_FILEPATH   = os.path.join(BASE_ARCHIVE_DIR, "search_index.json")
BLUESKY_HANDLE   = "smbccomics.bsky.social"
HEADERS          = {'User-Agent': 'SMBCSearchIndexerProject/1.0'}

os.makedirs(BASE_ARCHIVE_DIR, exist_ok=True)
# ---------------------

async def extract_via_chrome_lens_url(image_url, api_client):
    """
    Downloads an image into temporary buffer memory and streams it 
    directly into Google Lens to avoid persistent local disk writes.
    """
    if not image_url:
        return []
    try:
        response = requests.get(image_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
            
        # Write binary directly into a temp scratch file name for chrome-lens-py
        temp_filename = "lens_scratch_buffer.dat"
        with open(temp_filename, 'wb') as f:
            f.write(response.content)
            
        result = await api_client.process_image(image_path=temp_filename, output_format='full_text')
        
        # Clean scratch disk space safely
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
            
        text_payload = result.get('ocr_text', '').strip()
        if text_payload:
            return [line.strip() for line in text_payload.split('\n') if line.strip()]
        return []
    except Exception as e:
        print(f"    Google Lens URL OCR failure on {image_url}: {e}")
        return []

def extract_smbc_page_metadata(url):
    """
    Parses structural page elements, extracting canonical titles, dates,
    and returns absolute image source URLs for downstream direct OCR processing.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Parse Publication Date
        date_published = "Unknown"
        schema_script = soup.find('script', type='application/ld+json')
        if schema_script:
            try:
                schema_data = json.loads(schema_script.string)
                date_published = schema_data.get("datePublished", "")[:10]
            except (json.JSONDecodeError, TypeError):
                pass

        # 2. Extract Comic Title
        title_tag = soup.title
        comic_title = title_tag.string.replace("Saturday Morning Breakfast Cereal - ", "").strip() if title_tag else "Untitled"

        # 3. Target Main Panel Image Asset URL
        main_comic_url = None
        comic_img = soup.find('img', id='cc-comic')
        if comic_img and comic_img.get('src'):
            main_comic_url = comic_img.get('src')
            if main_comic_url.startswith('//'):
                main_comic_url = f"https:{main_comic_url}"

        # 4. Extract Hover Text
        hover_text = comic_img.get('title', '').strip() if comic_img else ""

        # 5. Target Votey Punchline Image Asset URL
        votey_url = None
        aftercomic_div = soup.find('div', id='aftercomic')
        if aftercomic_div:
            votey_img_tag = aftercomic_div.find('img')
            if votey_img_tag and votey_img_tag.get('src'):
                votey_url = votey_img_tag.get('src')
                if votey_url.startswith('//'):
                    votey_url = f"https:{votey_url}"

        return {
            "title": comic_title,
            "date": date_published,
            "hover_text": hover_text,
            "main_comic_url": main_comic_url,
            "votey_url": votey_url
        }
    except Exception as e:
        print(f"    Error parsing target layout at {url}: {e}")
        return None

async def run_bluesky_pipeline():
    client = Client(base_url='https://api.bsky.app')
    lens_api = LensAPI()
    
    print(f"Connecting to Bluesky... Syncing feed for: {BLUESKY_HANDLE}")
    profile = client.get_profile(actor=BLUESKY_HANDLE)
    
    # Load current master index array if it exists; otherwise start clean
    master_index = []
    if os.path.exists(INDEX_FILEPATH):
        try:
            with open(INDEX_FILEPATH, 'r', encoding='utf-8') as f:
                master_index = json.load(f)
        except Exception:
            master_index = []

    # Map existing IDs into a quick lookup hash to prevent duplicate scanning loops
    existing_ids = {entry["id"] for entry in master_index if "id" in entry}

    cursor = None
    processed_count = 0
    new_records_added = []

    feed_response = client.get_author_feed(actor=profile.did, cursor=cursor, limit=40)
    
    for item in feed_response.feed:
        post = item.post
        record = post.record
        target_url = None
        
        # Pull the absolute URL using your facet routing rule
        if hasattr(record, 'facets') and record.facets:
            for facet in record.facets:
                if hasattr(facet, 'features') and facet.features:
                    for feature in facet.features:
                        if hasattr(feature, 'uri') and "smbc-comics.com/comic/" in feature.uri:
                            target_url = feature.uri
                            break
                if target_url: break

        if not target_url and hasattr(record, 'embed') and record.embed:
            embed = record.embed
            if hasattr(embed, 'external') and embed.external and "smbc-comics.com/comic/" in embed.external.uri:
                target_url = embed.external.uri

        if target_url:
            target_url = target_url.split('#')[0].split('?')[0]
            if not target_url.startswith("http"):
                target_url = f"https://{target_url}"

            comic_id = target_url.split('/')[-1]
            
            # Skip if this comic is already saved in search_index.json
            if comic_id in existing_ids:
                continue

            print(f"\n[{processed_count + 1}] Processing New Entry: {target_url}")
            
            # Extract basic text fragments & image web nodes from the SMBC live site
            web_data = extract_smbc_page_metadata(target_url)
            if not web_data:
                continue

            # Capture direct Alt descriptions native to the Bluesky Post
            alt_text_segments = []
            if hasattr(record, 'embed') and record.embed:
                if hasattr(record.embed, 'images') and record.embed.images:
                    for img in record.embed.images:
                        if hasattr(img, 'alt') and img.alt:
                            alt_text_segments.append(img.alt.strip())
            bsky_alt = " ".join(alt_text_segments)

            # Fire memory buffer streams directly into Google Lens
            print("  Analyzing main comic layout with Google Lens...")
            main_ocr_lines = await extract_via_chrome_lens_url(web_data["main_comic_url"], lens_api)
            time.sleep(1.0) # Rate limit padding
            
            votey_ocr_lines = []
            if web_data["votey_url"]:
                print("  Analyzing votey punchline layout with Google Lens...")
                votey_ocr_lines = await extract_via_chrome_lens_url(web_data["votey_url"], lens_api)
                time.sleep(1.0)

            # Build structural sentences list exactly for the dynamic highlighting frontend
            combined_lines = []
            for item in main_ocr_lines + votey_ocr_lines + [web_data["hover_text"], bsky_alt]:
                if item and isinstance(item, str):
                    cleaned = " ".join(item.split())
                    if cleaned and cleaned not in combined_lines:
                        combined_lines.append(cleaned)

            new_entry = {
                "id": comic_id,
                "title": web_data["title"],
                "date": web_data["date"],
                "source_url": target_url,
                "lines": combined_lines
            }
            
            new_records_added.append(new_entry)
            processed_count += 1

    if new_records_added:
        # Prepend new entries so they appear at the top of the timeline
        master_index = new_records_added + master_index
        # Enforce clean database chronological reverse ordering (newest first)
        master_index.sort(key=lambda x: x.get("date", ""), reverse=True)
        
        with open(INDEX_FILEPATH, 'w', encoding='utf-8') as f:
            json.dump(master_index, f, indent=4, ensure_ascii=False)
        print(f"\nSuccessfully synced database index file. Added {len(new_records_added)} elements.")
    else:
        print("\nDatabase is already up to date. No operations required.")

if __name__ == "__main__":
    asyncio.run(run_bluesky_pipeline())