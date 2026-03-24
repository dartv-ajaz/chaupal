import os
import asyncio
from playwright.async_api import async_playwright

# Use a separate folder so it doesn't corrupt your daily Chrome data
PROFILE_DIR = "./chaupal_chrome_profile"

async def scrape_all_chaupal_content(page):
    print("🚀 Booting up the Catalog Scraper...")
    
    target_sections = [
        "https://www.chaupal.com/section/new-on-chaupal",
        "https://www.chaupal.com/section/trending-now",
        "https://www.chaupal.com/section/chaupal-originals"
    ]
    
    master_catalog = {}

    for section_url in target_sections:
        category_name = section_url.split('/')[-1].replace('-', ' ').upper()
        print(f"\n=========================================")
        print(f"🌐 SCANNING CATEGORY: {category_name}")
        print(f"=========================================\n")
        
        try:
            await page.goto(section_url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            continue

        print("⏳ Scrolling to load content...")
        previous_height = await page.evaluate("document.body.scrollHeight")
        while True:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000) 
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height == previous_height:
                break 
            previous_height = new_height

        links = await page.evaluate("""() => {
            let results = [];
            document.querySelectorAll('a').forEach(a => {
                let text = a.innerText.trim() || a.getAttribute('aria-label') || a.getAttribute('title') || 'Unknown Title';
                let href = a.href;
                
                if(href && (href.includes('/video/') || href.includes('/movie/') || 
                            href.includes('/details/') || href.includes('/tvshow/') || 
                            href.includes('/series/') || href.includes('/show/'))) {
                    results.push({title: text, url: href});
                }
            });
            return results;
        }""")
        
        for l in links:
            url = l['url']
            raw_title = l['title'].replace('\n', ' ').strip()
            title = url.split('/')[-1].replace('-', ' ').title() if (raw_title == 'Unknown Title' or raw_title == '') else raw_title
            
            if url not in master_catalog:
                master_catalog[url] = {"title": title, "category": category_name}

    return master_catalog


async def grab_stream(context, url):
    """Opens a tab, waits, and filters for the REAL movie stream."""
    page = await context.new_page()
    caught_streams = []
    
    def handle_request(req):
        url = req.url.lower()
        if ".m3u8" in url or ".mpd" in url:
            caught_streams.append(req.url)

    page.on("request", handle_request)
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000) 
        
        play_buttons = page.locator("button:has-text('Play'), a:has-text('Play'), .play-icon, .play-btn, [class*='episode']")
        if await play_buttons.count() > 0:
            await play_buttons.first.click()
        else:
            await page.mouse.click(500, 300) 
            
        # Give it a solid 8 seconds to negotiate DRM and switch from teaser to main movie
        print("   ⏳ Waiting 8 seconds for DRM negotiation...")
        await page.wait_for_timeout(8000)
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
    finally:
        await page.close() 
        
    # Analyze the caught streams to filter out the 3-minute teaser
    final_stream = None
    if caught_streams:
        # OTTs usually load the teaser first, then the main movie manifest. 
        # We want the LAST one it loaded, or specifically an .mpd file which is usually DRM protected.
        for stream in caught_streams:
            if "teaser" not in stream.lower() and "trailer" not in stream.lower():
                final_stream = stream
                
        # If we couldn't filter it out, just take the last one it grabbed
        if not final_stream:
            final_stream = caught_streams[-1]
            
    return final_stream


async def process_item(context, url, data, file_lock, output_filename, semaphore, index, total):
    async with semaphore:
        title = data['title']
        category = data['category']
        
        print(f"\n🎬 [{index}/{total}] Extracting: {title}...")
        stream = await grab_stream(context, url)
        
        if stream:
            print(f"   🔥 SUCCESS: {stream.split('?')[0][-30:]}")
            async with file_lock:
                with open(output_filename, "a", encoding="utf-8") as f:
                    clean_id = title.replace(" ", "").replace("&", "")
                    f.write(f'#EXTINF:-1 tvg-id="{clean_id}" group-title="{category}", {title}\n')
                    f.write(f'{stream}\n')
            return True
        else:
            print(f"   ⚠️ FAILED: No stream caught.")
            return False


async def main():
    print("\n=========================================")
    print("🔑 STEP 1: VERIFYING VIP LOGIN VIA GOOGLE CHROME")
    print("=========================================\n")
    
    async with async_playwright() as p:
        # 🚨 THE MAGIC FIX: channel="chrome" forces it to use your real, DRM-enabled Chrome
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome", # <--- THIS IS CRITICAL FOR WIDEVINE DRM
            headless=False, 
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        await page.goto("https://www.chaupal.com/")
        
        print("🛑 SCRIPT PAUSED.")
        print("👉 Since this is a new real Chrome profile, you MUST log in again.")
        print("👉 Play a premium movie for 10 seconds to generate the DRM keys.")
        input("✅ Press ENTER here ONLY AFTER the movie has played past the 3-minute teaser mark...")

        # Step 2: Scrape the URLs
        print("\n=========================================")
        print("🕸️ STEP 2: SCRAPING CATALOG")
        print("=========================================\n")
        full_catalog = await scrape_all_chaupal_content(page)
        
        # Step 3: Extract Streams
        print("\n=========================================")
        print("🔥 STEP 3: EXTRACTING PREMIUM STREAMS")
        print("=========================================\n")
        
        output_filename = "chaupal_master.m3u"
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")

        limit = 3 
        max_concurrent = 1 
        
        semaphore = asyncio.Semaphore(max_concurrent)
        file_lock = asyncio.Lock()
        tasks = []

        catalog_items = list(full_catalog.items())
        total_to_process = min(len(catalog_items), limit)

        for i in range(total_to_process):
            url, data = catalog_items[i]
            task = asyncio.create_task(
                process_item(context, url, data, file_lock, output_filename, semaphore, i+1, total_to_process)
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        successful_grabs = sum(1 for r in results if r is True)

        print("\n=========================================")
        print(f"🎉 TEST COMPLETE! Extracted {successful_grabs} streams.")
        print("=========================================\n")
        
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())