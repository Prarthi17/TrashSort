def _normalize_place_text(text: str) -> str:
    """Normalize free-text place names for better matching.
    - Trim and collapse spaces
    - Insert spaces before common suffixes when jammed e.g. 'ljuniversity' -> 'lj university'
    - Title-case words, but keep short acronyms uppercased (<=3 chars)
    """
    import re
    s = " ".join((text or "").strip().split())
    if not s:
        return s
    # Insert space before common words if jammed
    for word in ["university", "college", "institute", "technology", "school"]:
        s = re.sub(fr"(?i)([a-z])({word})", r"\1 \2", s)
    # Special case: 'lj' prefix
    s = re.sub(r"(?i)^lj\s*", "LJ ", s)
    # If still 'ljuniversity' pattern
    s = re.sub(r"(?i)^(lj)(university)", r"LJ University", s)
    # Title-case words except short acronyms
    parts = []
    for tok in s.split():
        if len(tok) <= 3 and tok.isalpha():
            parts.append(tok.upper())
        else:
            parts.append(tok.capitalize())
    return " ".join(parts)

from django.shortcuts import render 
from django.conf import settings
from django.http import JsonResponse
import os
import uuid
import requests
from PIL import Image, ImageDraw, ImageFont
# fetch_scrap_rates was removed from scraper; no longer imported
from .models import ContactMessage, Feedback
import json
try:
    import pandas as pd  # optional
except Exception:
    pd = None
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception:
    plt = None
import io
import base64
from .scraper import query_scrap_prices

def upload_view(request):
    """
    Renders an upload form (GET) and processes an uploaded image (POST).
    It sends the image to Roboflow, takes the highest-confidence prediction,
    draws a bounding box and label on the image, saves it to MEDIA, and
    returns the annotated result.
    """
    context = {}

    if request.method == 'POST':
        # Ensure media subdirectories
        input_dir = os.path.join(settings.MEDIA_ROOT, 'uploads')
        output_dir = os.path.join(settings.MEDIA_ROOT, 'results')
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        input_path = None
        input_name = None

        # 1) Prefer file upload if present
        if request.FILES.get('image'):
            up_file = request.FILES['image']
            ext = os.path.splitext(up_file.name)[1].lower() or '.jpg'
            input_name = f"{uuid.uuid4().hex}{ext}"
            input_path = os.path.join(input_dir, input_name)
            with open(input_path, 'wb+') as destination:
                for chunk in up_file.chunks():
                    destination.write(chunk)
        else:
            # 2) Else, try image URL
            image_url = (request.POST.get('image_url') or '').strip()
            if image_url:
                try:
                    r = requests.get(image_url, timeout=20)
                    r.raise_for_status()
                    # Basic content-type check
                    ctype = r.headers.get('Content-Type', '')
                    if 'image' not in ctype.lower():
                        context['error'] = 'Provided URL does not point to an image.'
                        return render(request, 'myapp/upload.html', context)
                    # Guess extension from content-type
                    ext = '.jpg'
                    if 'png' in ctype:
                        ext = '.png'
                    elif 'jpeg' in ctype:
                        ext = '.jpg'
                    elif 'webp' in ctype:
                        ext = '.webp'
                    elif 'bmp' in ctype:
                        ext = '.bmp'
                    input_name = f"{uuid.uuid4().hex}{ext}"
                    input_path = os.path.join(input_dir, input_name)
                    with open(input_path, 'wb') as f:
                        f.write(r.content)
                except Exception as e:
                    context['error'] = f"Failed to download image from URL: {e}"
                    return render(request, 'myapp/upload.html', context)
            else:
                context['error'] = 'Please choose a file or enter an image URL.'
                return render(request, 'myapp/upload.html', context)

        # Roboflow config (from the provided notebook logic)
        api_key = "JScqT0LRoryGBI6KwNkE"
        model = "trashsort-bfih9"
        version = 1

        # Call Roboflow Hosted Model
        with open(input_path, 'rb') as f:
            image_data = f.read()

        try:
            response = requests.post(
                f"https://detect.roboflow.com/{model}/{version}?api_key={api_key}",
                files={"file": image_data},
                data={"confidence": 40, "overlap": 30},
                timeout=30,
            )
            result = response.json()
            preds = result.get("predictions", [])
        except Exception as e:
            context['error'] = f"Failed to call Roboflow API: {e}"
            return render(request, 'myapp/upload.html', context)

        # Keep only highest confidence prediction
        top_pred = None
        if preds:
            top_pred = max(preds, key=lambda p: p.get("confidence", 0))

        # Load image and draw
        try:
            image = Image.open(input_path).convert("RGB")
            draw = ImageDraw.Draw(image)

            try:
                font = ImageFont.truetype("arial.ttf", 20)
            except Exception:
                font = ImageFont.load_default()

            if top_pred:
                x, y = top_pred.get("x", 0), top_pred.get("y", 0)
                w, h = top_pred.get("width", 0), top_pred.get("height", 0)
                class_name = top_pred.get("class", "object")
                conf = float(top_pred.get("confidence", 0))

                x0, y0 = x - w / 2, y - h / 2
                x1, y1 = x + w / 2, y + h / 2

                # Bounding box
                draw.rectangle([x0, y0, x1, y1], outline="red", width=4)

                # Label
                label = f"{class_name} ({conf:.2f})"
                # textbbox requires latest PIL; fallback if not available
                try:
                    bbox = draw.textbbox((x0, y0), label, font=font)
                    draw.rectangle(bbox, fill="red")
                except Exception:
                    # Rough background box
                    tw, th = draw.textlength(label, font=font), 20
                    draw.rectangle([x0, y0, x0 + tw + 6, y0 + th + 6], fill="red")
                draw.text((x0 + 3, y0 + 3), label, fill="white", font=font)

                context['pred_class'] = class_name
                context['pred_conf'] = f"{conf:.2f}"

                # Categorize detected waste type
                cls = (class_name or '').strip().lower()
                biodegradable_set = {"biological","organic","food","paper","cardboard","leaf","leaves","garden","yard"}
                recyclable_set = {"plastic","glass","pet","bottle","jar"}
                metal_set = {"metal","aluminum","aluminium","steel","iron","copper","tin"}
                hazardous_ewaste_set = {"battery","batteries","e-waste","ewaste","electronics","phone","laptop"}

                category = "General Waste"
                if cls in biodegradable_set:
                    category = "Biodegradable"
                elif cls in recyclable_set:
                    category = "Recyclable"
                elif cls in metal_set:
                    category = "Hazardous and Recyclable"
                elif cls in hazardous_ewaste_set:
                    category = "Hazardous E-waste"
                context['category'] = category

                # Generate solutions using Gemini based on detected class
                api_key = getattr(settings, 'GEMINI_API_KEY', '')
                if api_key:
                    try:
                        prompt = (
                            "You are ScrapSort, an expert in waste identification and disposal guidance.\n"
                            f"Detected item: {class_name}\n"
                            f"Category hint: {category}\n"
                            "Output plain text ONLY (no Markdown).\n"
                            "Structure EXACTLY as follows (do not omit any headers):\n"
                            "Category: <Biodegradable | Recyclable | Hazardous and Recyclable | Hazardous E-waste | General Waste>\n"
                            "Harm: Provide 15-20 short lines on environmental and health impact of this item type (each line as its own sentence, one per line).\n"
                            "Best Action: <one of Reduce | Reuse | Recycle | Responsible Disposal>\n"
                            "How to <Best Action>:\n"
                            "1. <short, concrete step>\n"
                            "2. <step>\n"
                            "3. <step>\n"
                            "4. <step>\n"
                            "5. <step>\n"
                            "6. <step>\n"
                            "7. <step>\n"
                            "8. <step>\n"
                            "Other Suggestions:\n"
                            "1. <tip>\n"
                            "2. <tip>\n"
                            "3. <tip>\n"
                            "4. <tip>\n"
                            "5. <tip>\n"
                            "6. <tip>\n"
                            "7. <tip>\n"
                            "8. <tip>\n"
                            "9. <tip>\n"
                            "10. <tip>\n"
                            "11. <tip>\n"
                            "12. <tip>\n"
                            "Rules: Keep language clear for the public; be factual; if uncertain, say what to check locally."
                        )
                        payload = {
                            "contents": [
                                {
                                    "parts": [
                                        {"text": prompt}
                                    ]
                                }
                            ]
                        }
                        url = (
                            "https://generativelanguage.googleapis.com/v1beta/models/" \
                            "gemini-1.5-flash:generateContent?key=" + api_key
                        )
                        resp = requests.post(url, json=payload, timeout=20)
                        if resp.status_code == 200:
                            data = resp.json()
                            # Extract first candidate text safely
                            text = ''
                            try:
                                text = data["candidates"][0]["content"]["parts"][0]["text"]
                            except Exception:
                                text = ''
                            if text:
                                # Sanitize any accidental markdown formatting
                                cleaned = text.replace('**', '')
                                # Normalize lines
                                raw_lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]

                                best_action = ''
                                how_to_label = ''
                                how_to = []
                                others_label = ''
                                others = []
                                category_out = ''
                                harm_out = ''

                                i = 0
                                n = len(raw_lines)
                                # Parse Category
                                if i < n and raw_lines[i].lower().startswith('category:'):
                                    category_out = raw_lines[i].split(':', 1)[1].strip()
                                    i += 1
                                # Parse Harm line(s) - collect until Best Action
                                if i < n and raw_lines[i].lower().startswith('harm:'):
                                    harm_out = raw_lines[i].split(':', 1)[1].strip()
                                    i += 1
                                    # Merge any following lines that do not start with a known header, preserving line breaks
                                    while i < n and not raw_lines[i].lower().startswith(('best action:', 'how to', 'other suggestions')):
                                        harm_out += ('\n' if harm_out else '') + raw_lines[i]
                                        i += 1
                                # Parse Best Action line
                                if i < n and raw_lines[i].lower().startswith('best action:'):
                                    best_action = raw_lines[i].split(':', 1)[1].strip()
                                    i += 1
                                # Parse How to section header
                                if i < n and raw_lines[i].lower().startswith('how to'):
                                    how_to_label = raw_lines[i]
                                    i += 1
                                # Collect How to items until Other Suggestions or end
                                while i < n and not raw_lines[i].lower().startswith('other suggestions'):
                                    item = raw_lines[i]
                                    # accept '-', or numbered '1. '
                                    if item.startswith('- '):
                                        item = item[2:].strip()
                                    else:
                                        import re
                                        item = re.sub(r'^\d+\.?\s+', '', item).strip()
                                    if item:
                                        how_to.append(item)
                                    i += 1
                                # Parse Other Suggestions header
                                if i < n and raw_lines[i].lower().startswith('other suggestions'):
                                    others_label = raw_lines[i]
                                    i += 1
                                # Collect remaining as other suggestions
                                while i < n:
                                    item = raw_lines[i]
                                    if item.startswith('- '):
                                        item = item[2:].strip()
                                    else:
                                        import re
                                        item = re.sub(r'^\d+\.?\s+', '', item).strip()
                                    if item:
                                        others.append(item)
                                    i += 1

                                # Fallback if model didn't follow exact structure
                                if not best_action and raw_lines:
                                    best_action = raw_lines[0]

                                # Enforce minimum counts using category defaults
                                cat_for_defaults = (category_out or context.get('category') or 'General Waste')
                                def _defaults_for(cat_name):
                                    cat = (cat_name or '').strip() or 'General Waste'
                                    defaults = {
                                        'Biodegradable': {
                                            'steps': [
                                                'Segregate kitchen scraps from recyclables.',
                                                'Collect greens (fruit/veg peels) and browns (dry leaves).',
                                                'Chop large pieces to speed up composting.',
                                                'Keep the compost slightly moist; avoid soggy piles.',
                                                'Turn the compost weekly to aerate.',
                                                'Exclude meat, fish, and oily food unless your setup allows.',
                                                'Use a lidded bin to deter pests and odors.',
                                                'Cure finished compost before using on plants.',
                                            ],
                                            'tips': [
                                                'Line the caddy with newspaper instead of plastic.',
                                                'Add dry leaves to balance wet food waste.',
                                                'Rinse caddy regularly to avoid flies.',
                                                'Freeze scraps if pickup is infrequent.',
                                                'Share compost with neighbors if you make extra.',
                                                'Avoid compostable plastics unless locally accepted.',
                                                'Crush eggshells for better breakdown.',
                                                'Bury fresh scraps under dry layer to reduce smell.',
                                                'Use finished compost as potting mix booster.',
                                                'Keep rainwater out to avoid leachate.',
                                                'Check local green-bin rules before adding citrus.',
                                                'Compost tea can be diluted for plants.',
                                            ],
                                            'harm': [
                                                'Organic waste in landfills generates methane, a potent greenhouse gas.',
                                                'Unmanaged piles attract flies, rodents, and stray animals.',
                                                'Leachate from rotting waste can contaminate soil and groundwater.',
                                                'Odors from decomposition degrade local air quality.',
                                                'Mixed organics can contaminate recyclables and reduce recovery.',
                                                'Transporting heavy wet waste increases fuel use and emissions.',
                                                'Landfilled organics occupy valuable landfill space.',
                                                'Improper handling may spread pathogens and pests.',
                                                'Food waste increases municipal management costs.',
                                                'Burning organics releases smoke and particulate matter.',
                                                'Composting avoids methane by enabling aerobic breakdown.',
                                                'Finished compost improves soil structure and fertility.',
                                                'Composting reduces need for chemical fertilizers.',
                                                'Community composting builds local circular systems.',
                                            ],
                                        },
                                        'Recyclable': {
                                            'steps': [
                                                'Rinse containers to remove food and liquids.',
                                                'Remove lids or labels if required locally.',
                                                'Flatten cardboard boxes to save bin space.',
                                                'Keep recyclables dry and free of organics.',
                                                'Check resin codes and local acceptance lists.',
                                                'Drop items at a certified recycling center.',
                                                'Do not bag recyclables in opaque plastic.',
                                                'Avoid mixing hazardous items with recyclables.',
                                            ],
                                            'tips': [
                                                'Prefer products with recycled content.',
                                                'Avoid black plastics that scanners miss.',
                                                'Use clear bags only if your city requires bags.',
                                                'Crush bottles to reduce volume (if accepted).',
                                                'Keep caps separate if not accepted together.',
                                                'Print local recycling guide and keep near bin.',
                                                'Bundle paper with twine rather than tape.',
                                                'Do not include greasy pizza boxes.',
                                                'Return deposit bottles to reclaim refunds.',
                                                'Locate e-waste drop-offs for electronics.',
                                                'Check special drop-offs for Styrofoam.',
                                                'Avoid wish-cycling—when in doubt, leave out.',
                                            ],
                                            'harm': [
                                                'Plastics can persist for centuries in landfills.',
                                                'Microplastics contaminate water and marine life.',
                                                'Wildlife can ingest or become entangled in waste.',
                                                'Unrecycled materials increase demand for virgin resources.',
                                                'Burning plastics releases toxic fumes and soot.',
                                                'Litter clogs drains, contributing to urban flooding.',
                                                'Recycling saves energy compared to producing new materials.',
                                                'Soil quality declines when plastics fragment in fields.',
                                                'Ocean gyres accumulate floating plastic debris.',
                                                'Inefficient disposal increases greenhouse gas emissions.',
                                                'Unsightly litter harms community well-being.',
                                                'Recycling supports green jobs and circular economies.',
                                                'Improper disposal raises municipal cleanup costs.',
                                                'Contamination in bins can spoil entire batches.',
                                            ],
                                        },
                                        'Hazardous and Recyclable': {
                                            'steps': [
                                                'Wear gloves to handle sharp or oily metal edges.',
                                                'Separate metals from general waste immediately.',
                                                'Bundle wires and cables to prevent tangles.',
                                                'Keep metals dry to reduce rust and contamination.',
                                                'Do not burn or bury metal items.',
                                                'Take metals to scrap dealers or city drop-offs.',
                                                'Transport heavy pieces safely to avoid injury.',
                                                'Request a weigh-slip or receipt where available.',
                                            ],
                                            'tips': [
                                                'Remove non-metal parts before recycling.',
                                                'Drain oil from machinery and dispose of oil properly.',
                                                'Store sharp pieces in sturdy containers.',
                                                'Keep magnets away from sensitive electronics.',
                                                'Sort by metal type if your yard pays more.',
                                                'Call ahead to confirm the accepted items.',
                                                'Avoid mixing metals with e-waste batteries.',
                                                'Use proper lifting techniques for heavy items.',
                                                'Clean off mud or debris to improve value.',
                                                'Photograph items for quotes if selling.',
                                                'Recycle aluminum foil if clean and balled.',
                                                'Check for local buyback programs.',
                                            ],
                                            'harm': [
                                                'Sharp metal edges can cause cuts and injuries.',
                                                'Oily residues can contaminate soil and waterways.',
                                                'Rust run-off may discolor and affect surfaces.',
                                                'Improper dumping can harm wildlife and pets.',
                                                'Illegal burning releases toxic metal fumes.',
                                                'Not recycling metals wastes significant energy value.',
                                                'Landfilled metals occupy space for decades.',
                                                'Loose wires can entangle animals and equipment.',
                                                'Leaking fluids from appliances can pollute sites.',
                                                'Poor handling increases workplace accidents.',
                                                'Unsorted metals reduce recycling efficiency.',
                                                'Recycling metals conserves ores and reduces mining impacts.',
                                                'Scrap theft and dumping burden communities.',
                                                'Responsible recycling supports circular metal flows.',
                                            ],
                                        },
                                        'Hazardous E-waste': {
                                            'steps': [
                                                'Back up and wipe personal data from devices.',
                                                'Remove detachable batteries where safe.',
                                                'Do not puncture or crush lithium cells.',
                                                'Place small batteries in taped terminals before drop-off.',
                                                'Keep e-waste dry and contained before transport.',
                                                'Use authorized e-waste centers only.',
                                                'Ask for a recycling/processing certificate.',
                                                'Package fragile screens to prevent breakage.',
                                            ],
                                            'tips': [
                                                'Trade-in programs can offset upgrade costs.',
                                                'Donate working devices to extend useful life.',
                                                'Avoid buying chargers you do not need.',
                                                'Use universal chargers to reduce clutter.',
                                                'Store batteries in a cool, dry place.',
                                                'Do not mix e-waste with household trash.',
                                                'Look for e-stewards or R2 certified facilities.',
                                                'Check manufacturer take-back schemes.',
                                                'Keep small e-waste in a labeled box.',
                                                'Remove cases and accessories before drop-off.',
                                                'Record serial numbers for asset tracking.',
                                                'Ask centers about secure data destruction.',
                                            ],
                                            'harm': [
                                                'E-waste can leach lead, mercury, and cadmium.',
                                                'Brominated flame retardants persist in the environment.',
                                                'Open burning produces dioxins and furans.',
                                                'Toxins can contaminate soil and groundwater.',
                                                'Informal recycling exposes workers to hazards.',
                                                'Damaged batteries may ignite and cause fires.',
                                                'Airborne dust can carry toxic particulates.',
                                                'Heavy metals bioaccumulate up the food chain.',
                                                'Contaminated sites can become sterile hotspots.',
                                                'Toxins may enter local food and water supplies.',
                                                'Poor practices harm community health outcomes.',
                                                'Certified recycling recovers valuable metals safely.',
                                                'Proper handling prevents fires during transport.',
                                                'Secure channels reduce illegal dumping and export.',
                                            ],
                                        },
                                        'General Waste': {
                                            'steps': [
                                                'Reduce single-use items by carrying reusables.',
                                                'Repair or donate items before disposal.',
                                                'Use the smallest bin liner necessary.',
                                                'Keep recyclables and organics separate.',
                                                'Follow local collection days and timings.',
                                                'Avoid overfilling bins to prevent litter.',
                                                'Report overflowing public bins to authorities.',
                                                'Track what you throw to find reduction opportunities.',
                                            ],
                                            'tips': [
                                                'Buy products with minimal packaging.',
                                                'Choose durable goods over disposable ones.',
                                                'Say no to freebies you will not use.',
                                                'Borrow or rent rarely used tools.',
                                                'Opt for refills and bulk purchases.',
                                                'Carry a reusable water bottle and bag.',
                                                'Plan meals to cut food waste.',
                                                'Use repair cafes or local fixers.',
                                                'Switch to cloth towels instead of paper.',
                                                'Avoid mixed-material packaging when possible.',
                                                'Unsubscribe from unwanted mailers.',
                                                'Teach family to sort waste correctly.',
                                            ],
                                            'harm': [
                                                'Mixed waste increases landfill volumes quickly.',
                                                'Decomposing waste emits methane and CO2.',
                                                'Odors and pests affect neighborhood quality of life.',
                                                'Leachate can pollute surface and groundwater.',
                                                'Incineration without controls releases toxic gases.',
                                                'Transporting waste consumes fuel and emits pollutants.',
                                                'Litter blocks drains and worsens flooding.',
                                                'Illegal dumping degrades public spaces.',
                                                'Poor segregation reduces recycling efficiency.',
                                                'Municipal cleanup costs burden taxpayers.',
                                                'Climate impacts rise with growing waste streams.',
                                                'Burning waste creates particulate pollution.',
                                                'Waste mismanagement harms urban biodiversity.',
                                                'Cleaner cities improve public health and safety.',
                                            ],
                                        },
                                    }
                                    return defaults.get(cat, defaults['General Waste'])

                                d = _defaults_for(cat_for_defaults)
                                # Pad How-to to 8
                                while len(how_to) < 8:
                                    how_to.append(d['steps'][len(how_to) % len(d['steps'])])
                                # Pad Other Suggestions to 12
                                while len(others) < 12:
                                    others.append(d['tips'][len(others) % len(d['tips'])])
                                # Pad Harm to 14 lines
                                if harm_out:
                                    harm_lines = [ln for ln in harm_out.split('\n') if ln.strip()]
                                else:
                                    harm_lines = []
                                while len(harm_lines) < 14:
                                    harm_lines.append(d['harm'][len(harm_lines) % len(d['harm'])])
                                harm_out = '\n'.join(harm_lines)

                                context['best_action'] = best_action
                                context['best_action_details'] = how_to
                                context['other_suggestions'] = others
                                # Override category if model returned one
                                if category_out:
                                    context['category'] = category_out
                                if harm_out:
                                    context['harm_text'] = harm_out
                                # Keep a plain text fallback for legacy template rendering
                                context['solutions_text'] = cleaned
                            else:
                                # Do not show placeholder text; hide suggestions section
                                context['solutions_text'] = ''
                        else:
                            # Graceful fallback on rate limits or other non-200 responses
                            # 14-line harm fallback per category
                            cat = context.get('category') or 'General Waste'
                            harm_map = {
                                'Biodegradable': '\n'.join([
                                    'Organic waste in landfills generates methane, a potent greenhouse gas.',
                                    'Unmanaged piles attract flies, rodents, and stray animals.',
                                    'Leachate from rotting waste can contaminate soil and groundwater.',
                                    'Odors from decomposition degrade local air quality.',
                                    'Mixed organics can contaminate recyclables and reduce recovery.',
                                    'Transporting heavy wet waste increases fuel use and emissions.',
                                    'Landfilled organics occupy valuable landfill space.',
                                    'Improper handling may spread pathogens and pests.',
                                    'Food waste increases municipal management costs.',
                                    'Burning organics releases smoke and particulate matter.',
                                    'Composting avoids methane by enabling aerobic breakdown.',
                                    'Finished compost improves soil structure and fertility.',
                                    'Composting reduces need for chemical fertilizers.',
                                    'Community composting builds local circular systems.',
                                ]),
                                'Recyclable': '\n'.join([
                                    'Plastics can persist for centuries in landfills.',
                                    'Microplastics contaminate water and marine life.',
                                    'Wildlife can ingest or become entangled in waste.',
                                    'Unrecycled materials increase demand for virgin resources.',
                                    'Burning plastics releases toxic fumes and soot.',
                                    'Litter clogs drains, contributing to urban flooding.',
                                    'Recycling saves energy compared to producing new materials.',
                                    'Soil quality declines when plastics fragment in fields.',
                                    'Ocean gyres accumulate floating plastic debris.',
                                    'Inefficient disposal increases greenhouse gas emissions.',
                                    'Unsightly litter harms community well-being.',
                                    'Recycling supports green jobs and circular economies.',
                                    'Improper disposal raises municipal cleanup costs.',
                                    'Contamination in bins can spoil entire batches.',
                                ]),
                                'Hazardous and Recyclable': '\n'.join([
                                    'Sharp metal edges can cause cuts and injuries.',
                                    'Oily residues can contaminate soil and waterways.',
                                    'Rust run-off may discolor and affect surfaces.',
                                    'Improper dumping can harm wildlife and pets.',
                                    'Illegal burning releases toxic metal fumes.',
                                    'Not recycling metals wastes significant energy value.',
                                    'Landfilled metals occupy space for decades.',
                                    'Loose wires can entangle animals and equipment.',
                                    'Leaking fluids from appliances can pollute sites.',
                                    'Poor handling increases workplace accidents.',
                                    'Unsorted metals reduce recycling efficiency.',
                                    'Recycling metals conserves ores and reduces mining impacts.',
                                    'Scrap theft and dumping burden communities.',
                                    'Responsible recycling supports circular metal flows.',
                                ]),
                                'Hazardous E-waste': '\n'.join([
                                    'E-waste can leach lead, mercury, and cadmium.',
                                    'Brominated flame retardants persist in the environment.',
                                    'Open burning produces dioxins and furans.',
                                    'Toxins can contaminate soil and groundwater.',
                                    'Informal recycling exposes workers to hazards.',
                                    'Damaged batteries may ignite and cause fires.',
                                    'Airborne dust can carry toxic particulates.',
                                    'Heavy metals bioaccumulate up the food chain.',
                                    'Contaminated sites can become sterile hotspots.',
                                    'Toxins may enter local food and water supplies.',
                                    'Poor practices harm community health outcomes.',
                                    'Certified recycling recovers valuable metals safely.',
                                    'Proper handling prevents fires during transport.',
                                    'Secure channels reduce illegal dumping and export.',
                                ]),
                                'General Waste': '\n'.join([
                                    'Mixed waste increases landfill volumes quickly.',
                                    'Decomposing waste emits methane and CO2.',
                                    'Odors and pests affect neighborhood quality of life.',
                                    'Leachate can pollute surface and groundwater.',
                                    'Incineration without controls releases toxic gases.',
                                    'Transporting waste consumes fuel and emits pollutants.',
                                    'Litter blocks drains and worsens flooding.',
                                    'Illegal dumping degrades public spaces.',
                                    'Poor segregation reduces recycling efficiency.',
                                    'Municipal cleanup costs burden taxpayers.',
                                    'Climate impacts rise with growing waste streams.',
                                    'Burning waste creates particulate pollution.',
                                    'Waste mismanagement harms urban biodiversity.',
                                    'Cleaner cities improve public health and safety.',
                                ]),
                            }
                            context['harm_text'] = context.get('harm_text') or harm_map.get(cat, harm_map['General Waste'])
                            # Hide suggestions block gracefully
                            context['solutions_text'] = ''
                            # Category-based fallback suggestions
                            if not context.get('best_action'):
                                cat = context.get('category') or 'General Waste'
                                fb = {
                                    'Biodegradable': (
                                        'Responsible Disposal',
                                        [
                                            'Segregate kitchen scraps from recyclables.',
                                            'Collect greens and browns to balance compost.',
                                            'Chop large pieces to speed up composting.',
                                            'Keep compost slightly moist; not waterlogged.',
                                            'Turn the compost weekly to aerate.',
                                            'Exclude meat and oily food unless allowed locally.',
                                            'Use a lidded bin to deter pests and odors.',
                                            'Cure finished compost before using on plants.',
                                        ],
                                        [
                                            'Line the caddy with newspaper instead of plastic.',
                                            'Add dry leaves to balance wet food waste.',
                                            'Rinse caddy regularly to avoid flies.',
                                            'Freeze scraps if pickup is infrequent.',
                                            'Share compost if you make extra.',
                                            'Avoid compostable plastics unless accepted.',
                                            'Crush eggshells for faster breakdown.',
                                            'Bury fresh scraps under a dry layer.',
                                            'Use finished compost as soil booster.',
                                            'Keep rainwater out to avoid leachate.',
                                            'Check local green-bin rules for citrus.',
                                            'Compost tea can be diluted for plants.',
                                        ]
                                    ),
                                    'Recyclable': (
                                        'Recycle',
                                        [
                                            'Rinse containers to remove food and liquids.',
                                            'Remove lids or labels if required locally.',
                                            'Flatten cardboard boxes to save bin space.',
                                            'Keep recyclables dry and free of organics.',
                                            'Check resin codes and local acceptance lists.',
                                            'Drop items at a certified recycling center.',
                                            'Do not bag recyclables in opaque plastic.',
                                            'Avoid mixing hazardous items with recyclables.',
                                        ],
                                        [
                                            'Prefer products with recycled content.',
                                            'Avoid black plastics that scanners miss.',
                                            'Use clear bags only if required by your city.',
                                            'Crush bottles to reduce volume if accepted.',
                                            'Keep caps separate if not accepted together.',
                                            'Print a local recycling guide near the bin.',
                                            'Bundle paper with twine rather than tape.',
                                            'Do not include greasy pizza boxes.',
                                            'Return deposit bottles to reclaim refunds.',
                                            'Locate e-waste drop-offs for electronics.',
                                            'Check special drop-offs for Styrofoam.',
                                            'Avoid wish-cycling; when in doubt, leave out.',
                                        ]
                                    ),
                                    'Hazardous and Recyclable': (
                                        'Responsible Disposal',
                                        [
                                            'Wear gloves to handle sharp or oily metal edges.',
                                            'Separate metals from general waste immediately.',
                                            'Bundle wires and cables to prevent tangles.',
                                            'Keep metals dry to reduce rust and contamination.',
                                            'Do not burn or bury metal items.',
                                            'Take metals to scrap dealers or city drop-offs.',
                                            'Transport heavy pieces safely to avoid injury.',
                                            'Request a weigh-slip or receipt where available.',
                                        ],
                                        [
                                            'Remove non-metal parts before recycling.',
                                            'Drain oil from machinery and dispose of oil properly.',
                                            'Store sharp pieces in sturdy containers.',
                                            'Keep magnets away from sensitive electronics.',
                                            'Sort by metal type if your yard pays more.',
                                            'Call ahead to confirm accepted items.',
                                            'Avoid mixing metals with e-waste batteries.',
                                            'Use proper lifting techniques for heavy items.',
                                            'Clean off mud or debris to improve value.',
                                            'Photograph items for quotes if selling.',
                                            'Recycle aluminum foil if clean and balled.',
                                            'Check for local buyback programs.',
                                        ]
                                    ),
                                    'Hazardous E-waste': (
                                        'Responsible Disposal',
                                        [
                                            'Back up and wipe personal data from devices.',
                                            'Remove detachable batteries where safe.',
                                            'Do not puncture or crush lithium cells.',
                                            'Place small batteries with terminals taped before drop-off.',
                                            'Keep e-waste dry and contained before transport.',
                                            'Use authorized e-waste centers only.',
                                            'Ask for a recycling or processing certificate.',
                                            'Package fragile screens to prevent breakage.',
                                        ],
                                        [
                                            'Trade-in programs can offset upgrade costs.',
                                            'Donate working devices to extend useful life.',
                                            'Avoid buying chargers you do not need.',
                                            'Use universal chargers to reduce clutter.',
                                            'Store batteries in a cool, dry place.',
                                            'Do not mix e-waste with household trash.',
                                            'Look for e-stewards or R2 certified facilities.',
                                            'Check manufacturer take-back schemes.',
                                            'Keep small e-waste in a labeled box.',
                                            'Remove cases and accessories before drop-off.',
                                            'Record serial numbers for asset tracking.',
                                            'Ask centers about secure data destruction.',
                                        ]
                                    ),
                                    'General Waste': (
                                        'Reduce',
                                        [
                                            'Reduce single-use items by carrying reusables.',
                                            'Repair or donate items before disposal.',
                                            'Use the smallest bin liner necessary.',
                                            'Keep recyclables and organics separate.',
                                            'Follow local collection days and timings.',
                                            'Avoid overfilling bins to prevent litter.',
                                            'Report overflowing public bins to authorities.',
                                            'Track what you throw to find reduction opportunities.',
                                        ],
                                        [
                                            'Buy products with minimal packaging.',
                                            'Choose durable goods over disposable ones.',
                                            'Say no to freebies you will not use.',
                                            'Borrow or rent rarely used tools.',
                                            'Opt for refills and bulk purchases.',
                                            'Carry a reusable water bottle and bag.',
                                            'Plan meals to cut food waste.',
                                            'Use repair cafes or local fixers.',
                                            'Switch to cloth towels instead of paper.',
                                            'Avoid mixed-material packaging when possible.',
                                            'Unsubscribe from unwanted mailers.',
                                            'Teach family to sort waste correctly.',
                                        ]
                                    ),
                                }.get(cat)
                                if fb:
                                    ba, steps, tips = fb
                                    context['best_action'] = ba
                                    context['best_action_details'] = steps
                                    context['other_suggestions'] = tips
                    except Exception as e:
                        # Fallback harm text if API fails
                        # 14-line harm fallback per category (same map as above)
                        cat = context.get('category') or 'General Waste'
                        harm_map = {
                            'Biodegradable': '\n'.join([
                                'Organic waste in landfills generates methane, a potent greenhouse gas.',
                                'Unmanaged piles attract flies, rodents, and stray animals.',
                                'Leachate from rotting waste can contaminate soil and groundwater.',
                                'Odors from decomposition degrade local air quality.',
                                'Mixed organics can contaminate recyclables and reduce recovery.',
                                'Transporting heavy wet waste increases fuel use and emissions.',
                                'Landfilled organics occupy valuable landfill space.',
                                'Improper handling may spread pathogens and pests.',
                                'Food waste increases municipal management costs.',
                                'Burning organics releases smoke and particulate matter.',
                                'Composting avoids methane by enabling aerobic breakdown.',
                                'Finished compost improves soil structure and fertility.',
                                'Composting reduces need for chemical fertilizers.',
                                'Community composting builds local circular systems.',
                            ]),
                            'Recyclable': '\n'.join([
                                'Plastics can persist for centuries in landfills.',
                                'Microplastics contaminate water and marine life.',
                                'Wildlife can ingest or become entangled in waste.',
                                'Unrecycled materials increase demand for virgin resources.',
                                'Burning plastics releases toxic fumes and soot.',
                                'Litter clogs drains, contributing to urban flooding.',
                                'Recycling saves energy compared to producing new materials.',
                                'Soil quality declines when plastics fragment in fields.',
                                'Ocean gyres accumulate floating plastic debris.',
                                'Inefficient disposal increases greenhouse gas emissions.',
                                'Unsightly litter harms community well-being.',
                                'Recycling supports green jobs and circular economies.',
                                'Improper disposal raises municipal cleanup costs.',
                                'Contamination in bins can spoil entire batches.',
                            ]),
                            'Hazardous and Recyclable': '\n'.join([
                                'Sharp metal edges can cause cuts and injuries.',
                                'Oily residues can contaminate soil and waterways.',
                                'Rust run-off may discolor and affect surfaces.',
                                'Improper dumping can harm wildlife and pets.',
                                'Illegal burning releases toxic metal fumes.',
                                'Not recycling metals wastes significant energy value.',
                                'Landfilled metals occupy space for decades.',
                                'Loose wires can entangle animals and equipment.',
                                'Leaking fluids from appliances can pollute sites.',
                                'Poor handling increases workplace accidents.',
                                'Unsorted metals reduce recycling efficiency.',
                                'Recycling metals conserves ores and reduces mining impacts.',
                                'Scrap theft and dumping burden communities.',
                                'Responsible recycling supports circular metal flows.',
                            ]),
                            'Hazardous E-waste': '\n'.join([
                                'E-waste can leach lead, mercury, and cadmium.',
                                'Brominated flame retardants persist in the environment.',
                                'Open burning produces dioxins and furans.',
                                'Toxins can contaminate soil and groundwater.',
                                'Informal recycling exposes workers to hazards.',
                                'Damaged batteries may ignite and cause fires.',
                                'Airborne dust can carry toxic particulates.',
                                'Heavy metals bioaccumulate up the food chain.',
                                'Contaminated sites can become sterile hotspots.',
                                'Toxins may enter local food and water supplies.',
                                'Poor practices harm community health outcomes.',
                                'Certified recycling recovers valuable metals safely.',
                                'Proper handling prevents fires during transport.',
                                'Secure channels reduce illegal dumping and export.',
                            ]),
                            'General Waste': '\n'.join([
                                'Mixed waste increases landfill volumes quickly.',
                                'Decomposing waste emits methane and CO2.',
                                'Odors and pests affect neighborhood quality of life.',
                                'Leachate can pollute surface and groundwater.',
                                'Incineration without controls releases toxic gases.',
                                'Transporting waste consumes fuel and emits pollutants.',
                                'Litter blocks drains and worsens flooding.',
                                'Illegal dumping degrades public spaces.',
                                'Poor segregation reduces recycling efficiency.',
                                'Municipal cleanup costs burden taxpayers.',
                                'Climate impacts rise with growing waste streams.',
                                'Burning waste creates particulate pollution.',
                                'Waste mismanagement harms urban biodiversity.',
                                'Cleaner cities improve public health and safety.',
                            ]),
                        }
                        context['harm_text'] = harm_map.get(cat, harm_map['General Waste'])
                        # Hide suggestions block on exception
                        context['solutions_text'] = ''
                        # Category-based fallback suggestions (8 steps, 12 tips)
                        if not context.get('best_action'):
                            cat = context.get('category') or 'General Waste'
                            fb = {
                                'Biodegradable': (
                                    'Responsible Disposal',
                                    [
                                        'Segregate kitchen scraps from recyclables.',
                                        'Collect greens and browns to balance compost.',
                                        'Chop large pieces to speed up composting.',
                                        'Keep compost slightly moist; not waterlogged.',
                                        'Turn the compost weekly to aerate.',
                                        'Exclude meat and oily food unless allowed locally.',
                                        'Use a lidded bin to deter pests and odors.',
                                        'Cure finished compost before using on plants.',
                                    ],
                                    [
                                        'Line the caddy with newspaper instead of plastic.',
                                        'Add dry leaves to balance wet food waste.',
                                        'Rinse caddy regularly to avoid flies.',
                                        'Freeze scraps if pickup is infrequent.',
                                        'Share compost if you make extra.',
                                        'Avoid compostable plastics unless accepted.',
                                        'Crush eggshells for faster breakdown.',
                                        'Bury fresh scraps under a dry layer.',
                                        'Use finished compost as soil booster.',
                                        'Keep rainwater out to avoid leachate.',
                                        'Check local green-bin rules for citrus.',
                                        'Compost tea can be diluted for plants.',
                                    ]
                                ),
                                'Recyclable': (
                                    'Recycle',
                                    [
                                        'Rinse containers to remove food and liquids.',
                                        'Remove lids or labels if required locally.',
                                        'Flatten cardboard boxes to save bin space.',
                                        'Keep recyclables dry and free of organics.',
                                        'Check resin codes and local acceptance lists.',
                                        'Drop items at a certified recycling center.',
                                        'Do not bag recyclables in opaque plastic.',
                                        'Avoid mixing hazardous items with recyclables.',
                                    ],
                                    [
                                        'Prefer products with recycled content.',
                                        'Avoid black plastics that scanners miss.',
                                        'Use clear bags only if required by your city.',
                                        'Crush bottles to reduce volume if accepted.',
                                        'Keep caps separate if not accepted together.',
                                        'Print a local recycling guide near the bin.',
                                        'Bundle paper with twine rather than tape.',
                                        'Do not include greasy pizza boxes.',
                                        'Return deposit bottles to reclaim refunds.',
                                        'Locate e-waste drop-offs for electronics.',
                                        'Check special drop-offs for Styrofoam.',
                                        'Avoid wish-cycling; when in doubt, leave out.',
                                    ]
                                ),
                                'Hazardous and Recyclable': (
                                    'Responsible Disposal',
                                    [
                                        'Wear gloves to handle sharp or oily metal edges.',
                                        'Separate metals from general waste immediately.',
                                        'Bundle wires and cables to prevent tangles.',
                                        'Keep metals dry to reduce rust and contamination.',
                                        'Do not burn or bury metal items.',
                                        'Take metals to scrap dealers or city drop-offs.',
                                        'Transport heavy pieces safely to avoid injury.',
                                        'Request a weigh-slip or receipt where available.',
                                    ],
                                    [
                                        'Remove non-metal parts before recycling.',
                                        'Drain oil from machinery and dispose of oil properly.',
                                        'Store sharp pieces in sturdy containers.',
                                        'Keep magnets away from sensitive electronics.',
                                        'Sort by metal type if your yard pays more.',
                                        'Call ahead to confirm accepted items.',
                                        'Avoid mixing metals with e-waste batteries.',
                                        'Use proper lifting techniques for heavy items.',
                                        'Clean off mud or debris to improve value.',
                                        'Photograph items for quotes if selling.',
                                        'Recycle aluminum foil if clean and balled.',
                                        'Check for local buyback programs.',
                                    ]
                                ),
                                'Hazardous E-waste': (
                                    'Responsible Disposal',
                                    [
                                        'Back up and wipe personal data from devices.',
                                        'Remove detachable batteries where safe.',
                                        'Do not puncture or crush lithium cells.',
                                        'Place small batteries with terminals taped before drop-off.',
                                        'Keep e-waste dry and contained before transport.',
                                        'Use authorized e-waste centers only.',
                                        'Ask for a recycling or processing certificate.',
                                        'Package fragile screens to prevent breakage.',
                                    ],
                                    [
                                        'Trade-in programs can offset upgrade costs.',
                                        'Donate working devices to extend useful life.',
                                        'Avoid buying chargers you do not need.',
                                        'Use universal chargers to reduce clutter.',
                                        'Store batteries in a cool, dry place.',
                                        'Do not mix e-waste with household trash.',
                                        'Look for e-stewards or R2 certified facilities.',
                                        'Check manufacturer take-back schemes.',
                                        'Keep small e-waste in a labeled box.',
                                        'Remove cases and accessories before drop-off.',
                                        'Record serial numbers for asset tracking.',
                                        'Ask centers about secure data destruction.',
                                    ]
                                ),
                                'General Waste': (
                                    'Reduce',
                                    [
                                        'Reduce single-use items by carrying reusables.',
                                        'Repair or donate items before disposal.',
                                        'Use the smallest bin liner necessary.',
                                        'Keep recyclables and organics separate.',
                                        'Follow local collection days and timings.',
                                        'Avoid overfilling bins to prevent litter.',
                                        'Report overflowing public bins to authorities.',
                                        'Track what you throw to find reduction opportunities.',
                                    ],
                                    [
                                        'Buy products with minimal packaging.',
                                        'Choose durable goods over disposable ones.',
                                        'Say no to freebies you will not use.',
                                        'Borrow or rent rarely used tools.',
                                        'Opt for refills and bulk purchases.',
                                        'Carry a reusable water bottle and bag.',
                                        'Plan meals to cut food waste.',
                                        'Use repair cafes or local fixers.',
                                        'Switch to cloth towels instead of paper.',
                                        'Avoid mixed-material packaging when possible.',
                                        'Unsubscribe from unwanted mailers.',
                                        'Teach family to sort waste correctly.',
                                    ]
                                ),
                            }.get(cat)
                            if fb:
                                ba, steps, tips = fb
                                context['best_action'] = ba
                                context['best_action_details'] = steps
                                context['other_suggestions'] = tips
                else:
                    # 14-line harm fallback when Gemini disabled
                    cat = context.get('category') or 'General Waste'
                    harm_map = {
                        'Biodegradable': '\n'.join([
                            'Organic waste in landfills generates methane, a potent greenhouse gas.',
                            'Unmanaged piles attract flies, rodents, and stray animals.',
                            'Leachate from rotting waste can contaminate soil and groundwater.',
                            'Odors from decomposition degrade local air quality.',
                            'Mixed organics can contaminate recyclables and reduce recovery.',
                            'Transporting heavy wet waste increases fuel use and emissions.',
                            'Landfilled organics occupy valuable landfill space.',
                            'Improper handling may spread pathogens and pests.',
                            'Food waste increases municipal management costs.',
                            'Burning organics releases smoke and particulate matter.',
                            'Composting avoids methane by enabling aerobic breakdown.',
                            'Finished compost improves soil structure and fertility.',
                            'Composting reduces need for chemical fertilizers.',
                            'Community composting builds local circular systems.',
                        ]),
                        'Recyclable': '\n'.join([
                            'Plastics can persist for centuries in landfills.',
                            'Microplastics contaminate water and marine life.',
                            'Wildlife can ingest or become entangled in waste.',
                            'Unrecycled materials increase demand for virgin resources.',
                            'Burning plastics releases toxic fumes and soot.',
                            'Litter clogs drains, contributing to urban flooding.',
                            'Recycling saves energy compared to producing new materials.',
                            'Soil quality declines when plastics fragment in fields.',
                            'Ocean gyres accumulate floating plastic debris.',
                            'Inefficient disposal increases greenhouse gas emissions.',
                            'Unsightly litter harms community well-being.',
                            'Recycling supports green jobs and circular economies.',
                            'Improper disposal raises municipal cleanup costs.',
                            'Contamination in bins can spoil entire batches.',
                        ]),
                        'Hazardous and Recyclable': '\n'.join([
                            'Sharp metal edges can cause cuts and injuries.',
                            'Oily residues can contaminate soil and waterways.',
                            'Rust run-off may discolor and affect surfaces.',
                            'Improper dumping can harm wildlife and pets.',
                            'Illegal burning releases toxic metal fumes.',
                            'Not recycling metals wastes significant energy value.',
                            'Landfilled metals occupy space for decades.',
                            'Loose wires can entangle animals and equipment.',
                            'Leaking fluids from appliances can pollute sites.',
                            'Poor handling increases workplace accidents.',
                            'Unsorted metals reduce recycling efficiency.',
                            'Recycling metals conserves ores and reduces mining impacts.',
                            'Scrap theft and dumping burden communities.',
                            'Responsible recycling supports circular metal flows.',
                        ]),
                        'Hazardous E-waste': '\n'.join([
                            'E-waste can leach lead, mercury, and cadmium.',
                            'Brominated flame retardants persist in the environment.',
                            'Open burning produces dioxins and furans.',
                            'Toxins can contaminate soil and groundwater.',
                            'Informal recycling exposes workers to hazards.',
                            'Damaged batteries may ignite and cause fires.',
                            'Airborne dust can carry toxic particulates.',
                            'Heavy metals bioaccumulate up the food chain.',
                            'Contaminated sites can become sterile hotspots.',
                            'Toxins may enter local food and water supplies.',
                            'Poor practices harm community health outcomes.',
                            'Certified recycling recovers valuable metals safely.',
                            'Proper handling prevents fires during transport.',
                            'Secure channels reduce illegal dumping and export.',
                        ]),
                        'General Waste': '\n'.join([
                            'Mixed waste increases landfill volumes quickly.',
                            'Decomposing waste emits methane and CO2.',
                            'Odors and pests affect neighborhood quality of life.',
                            'Leachate can pollute surface and groundwater.',
                            'Incineration without controls releases toxic gases.',
                            'Transporting waste consumes fuel and emits pollutants.',
                            'Litter blocks drains and worsens flooding.',
                            'Illegal dumping degrades public spaces.',
                            'Poor segregation reduces recycling efficiency.',
                            'Municipal cleanup costs burden taxpayers.',
                            'Climate impacts rise with growing waste streams.',
                            'Burning waste creates particulate pollution.',
                            'Waste mismanagement harms urban biodiversity.',
                            'Cleaner cities improve public health and safety.',
                        ]),
                    }
                    context['harm_text'] = harm_map.get(cat, harm_map['General Waste'])
                    # Hide suggestions when Gemini is disabled
                    context['solutions_text'] = ''
                    # Category-based fallback suggestions (8 steps, 12 tips)
                    if not context.get('best_action'):
                        cat = context.get('category') or 'General Waste'
                        fb = {
                            'Biodegradable': (
                                'Responsible Disposal',
                                [
                                    'Segregate kitchen scraps from recyclables.',
                                    'Collect greens and browns to balance compost.',
                                    'Chop large pieces to speed up composting.',
                                    'Keep compost slightly moist; not waterlogged.',
                                    'Turn the compost weekly to aerate.',
                                    'Exclude meat and oily food unless allowed locally.',
                                    'Use a lidded bin to deter pests and odors.',
                                    'Cure finished compost before using on plants.',
                                ],
                                [
                                    'Line the caddy with newspaper instead of plastic.',
                                    'Add dry leaves to balance wet food waste.',
                                    'Rinse caddy regularly to avoid flies.',
                                    'Freeze scraps if pickup is infrequent.',
                                    'Share compost if you make extra.',
                                    'Avoid compostable plastics unless accepted.',
                                    'Crush eggshells for faster breakdown.',
                                    'Bury fresh scraps under a dry layer.',
                                    'Use finished compost as soil booster.',
                                    'Keep rainwater out to avoid leachate.',
                                    'Check local green-bin rules for citrus.',
                                    'Compost tea can be diluted for plants.',
                                ]
                            ),
                            'Recyclable': (
                                'Recycle',
                                [
                                    'Rinse containers to remove food and liquids.',
                                    'Remove lids or labels if required locally.',
                                    'Flatten cardboard boxes to save bin space.',
                                    'Keep recyclables dry and free of organics.',
                                    'Check resin codes and local acceptance lists.',
                                    'Drop items at a certified recycling center.',
                                    'Do not bag recyclables in opaque plastic.',
                                    'Avoid mixing hazardous items with recyclables.',
                                ],
                                [
                                    'Prefer products with recycled content.',
                                    'Avoid black plastics that scanners miss.',
                                    'Use clear bags only if required by your city.',
                                    'Crush bottles to reduce volume if accepted.',
                                    'Keep caps separate if not accepted together.',
                                    'Print a local recycling guide near the bin.',
                                    'Bundle paper with twine rather than tape.',
                                    'Do not include greasy pizza boxes.',
                                    'Return deposit bottles to reclaim refunds.',
                                    'Locate e-waste drop-offs for electronics.',
                                    'Check special drop-offs for Styrofoam.',
                                    'Avoid wish-cycling; when in doubt, leave out.',
                                ]
                            ),
                            'Hazardous and Recyclable': (
                                'Responsible Disposal',
                                [
                                    'Wear gloves to handle sharp or oily metal edges.',
                                    'Separate metals from general waste immediately.',
                                    'Bundle wires and cables to prevent tangles.',
                                    'Keep metals dry to reduce rust and contamination.',
                                    'Do not burn or bury metal items.',
                                    'Take metals to scrap dealers or city drop-offs.',
                                    'Transport heavy pieces safely to avoid injury.',
                                    'Request a weigh-slip or receipt where available.',
                                ],
                                [
                                    'Remove non-metal parts before recycling.',
                                    'Drain oil from machinery and dispose of oil properly.',
                                    'Store sharp pieces in sturdy containers.',
                                    'Keep magnets away from sensitive electronics.',
                                    'Sort by metal type if your yard pays more.',
                                    'Call ahead to confirm accepted items.',
                                    'Avoid mixing metals with e-waste batteries.',
                                    'Use proper lifting techniques for heavy items.',
                                    'Clean off mud or debris to improve value.',
                                    'Photograph items for quotes if selling.',
                                    'Recycle aluminum foil if clean and balled.',
                                    'Check for local buyback programs.',
                                ]
                            ),
                            'Hazardous E-waste': (
                                'Responsible Disposal',
                                [
                                    'Back up and wipe personal data from devices.',
                                    'Remove detachable batteries where safe.',
                                    'Do not puncture or crush lithium cells.',
                                    'Place small batteries with terminals taped before drop-off.',
                                    'Keep e-waste dry and contained before transport.',
                                    'Use authorized e-waste centers only.',
                                    'Ask for a recycling or processing certificate.',
                                    'Package fragile screens to prevent breakage.',
                                ],
                                [
                                    'Trade-in programs can offset upgrade costs.',
                                    'Donate working devices to extend useful life.',
                                    'Avoid buying chargers you do not need.',
                                    'Use universal chargers to reduce clutter.',
                                    'Store batteries in a cool, dry place.',
                                    'Do not mix e-waste with household trash.',
                                    'Look for e-stewards or R2 certified facilities.',
                                    'Check manufacturer take-back schemes.',
                                    'Keep small e-waste in a labeled box.',
                                    'Remove cases and accessories before drop-off.',
                                    'Record serial numbers for asset tracking.',
                                    'Ask centers about secure data destruction.',
                                ]
                            ),
                            'General Waste': (
                                'Reduce',
                                [
                                    'Reduce single-use items by carrying reusables.',
                                    'Repair or donate items before disposal.',
                                    'Use the smallest bin liner necessary.',
                                    'Keep recyclables and organics separate.',
                                    'Follow local collection days and timings.',
                                    'Avoid overfilling bins to prevent litter.',
                                    'Report overflowing public bins to authorities.',
                                    'Track what you throw to find reduction opportunities.',
                                ],
                                [
                                    'Buy products with minimal packaging.',
                                    'Choose durable goods over disposable ones.',
                                    'Say no to freebies you will not use.',
                                    'Borrow or rent rarely used tools.',
                                    'Opt for refills and bulk purchases.',
                                    'Carry a reusable water bottle and bag.',
                                    'Plan meals to cut food waste.',
                                    'Use repair cafes or local fixers.',
                                    'Switch to cloth towels instead of paper.',
                                    'Avoid mixed-material packaging when possible.',
                                    'Unsubscribe from unwanted mailers.',
                                    'Teach family to sort waste correctly.',
                                ]
                            ),
                        }.get(cat)
                        if fb:
                            ba, steps, tips = fb
                            context['best_action'] = ba
                            context['best_action_details'] = steps
                            context['other_suggestions'] = tips
            else:
                context['message'] = "No objects detected above threshold."

            # Save annotated image
            output_name = f"annotated_{input_name}"
            output_path = os.path.join(output_dir, output_name)
            image.save(output_path)

            # Build URLs
            context['result_url'] = f"{settings.MEDIA_URL}results/{output_name}"
            context['original_url'] = f"{settings.MEDIA_URL}uploads/{input_name}"

        except Exception as e:
            context['error'] = f"Image processing failed: {e}"

    return render(request, 'myapp/upload.html', context)


# Simple pages for navbar
def scrap_price(request):
    """CSV-only: read from media/scrapping_prices.csv, filter by 'item', sort by price desc."""
    item = (request.GET.get("item") or request.POST.get("item") or "").strip()
    weight_raw = (request.GET.get("weight") or request.POST.get("weight") or "").strip()
    # Absolute path provided by user
    csv_abs_path = r"D:/yolo/trashsort/trashsort/media/scrapping_prices.csv"
    # Use the scraper helper to load, normalize and filter
    try:
        rows = query_scrap_prices(item, filename=csv_abs_path)
    except Exception:
        rows = []

    # Build per-website max price (in case multiple items matched)
    site_to_price = {}
    for r in rows:
        try:
            site = str(r.get("Website", "")).strip()
            price = float(r.get("Price", 0) or 0)
        except Exception:
            continue
        if not site:
            continue
        if site not in site_to_price or price > site_to_price[site]:
            site_to_price[site] = price

    # Prepare labels and data for graph (sorted by price desc)
    site_items_sorted = sorted(site_to_price.items(), key=lambda x: x[1], reverse=True)
    site_labels = [s for s, _ in site_items_sorted]
    site_prices = [p for _, p in site_items_sorted]

    # Highest price and weight-based calculation
    max_price = site_prices[0] if site_prices else None
    top_site = site_labels[0] if site_labels else None
    weight_value = None
    total_value = None
    if weight_raw:
        try:
            weight_value = float(weight_raw)
            if max_price is not None:
                total_value = weight_value * float(max_price)
        except Exception:
            weight_value = None

    # Build matplotlib graph image if possible
    graph_b64 = None
    if plt is not None and site_labels and site_prices:
        try:
            fig, ax = plt.subplots(figsize=(20, 8))
            ax.plot(site_labels, site_prices, marker='o', color='#16a34a', linewidth=3, markersize=6)
            ax.set_xlabel('Website', fontsize=16, fontweight='bold')
            ax.set_ylabel('Price (₹/kg)', fontsize=16, fontweight='bold')
            ax.set_title('Price Comparison by Website', fontsize=18, fontweight='bold')
            ax.grid(True, linestyle='--', alpha=0.3)
            ax.tick_params(axis='both', labelsize=14)
            # Bold tick labels
            for lbl in ax.get_xticklabels():
                try:
                    lbl.set_fontweight('bold')
                except Exception:
                    pass
            for lbl in ax.get_yticklabels():
                try:
                    lbl.set_fontweight('bold')
                except Exception:
                    pass
            plt.xticks(rotation=25, ha='right')
            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=200)
            plt.close(fig)
            buf.seek(0)
            graph_b64 = base64.b64encode(buf.read()).decode('ascii')
        except Exception:
            graph_b64 = None

    # Site lists for template (optional usage)
    BULK_WASTE_SITES = [
        {"name": "Trash To Cash", "url": "https://trashtocash.co.in/index.html"},
        {"name": "ScrapEco", "url": "https://www.scrapeco.in/"},
        {"name": "Saahas Zero Waste", "url": "https://saahaszerowaste.com/"},
        {"name": "ScrapDeal", "url": "https://www.scrapdeal.co.in/doortodoorpickup.php"},
        {"name": "The Bhangarwala", "url": "https://thebhangarwala.in/"},
    ]
    SCRAP_SITES = [
        {"name": "TheKabadiwala", "url": "https://www.thekabadiwala.com/scrap-rates/Ahmadabad"},
        {"name": "RecyclePay", "url": "https://recyclepay.ceibagreen.com/price-list/"},
        {"name": "ScrapBuddy", "url": "http://scrapbuddy.in/ratecard"},
        {"name": "RecycleBaba", "url": "https://recyclebaba.com/scrap-price-list/"},
        {"name": "KabadiwalaOnline", "url": "https://www.kabadiwalaonline.com/scrap-rates/"},
        {"name": "ScrapUncle", "url": "https://scrapuncle.com/local-rate"},
    ]

    return render(
        request,
        "myapp/scrap_price.html",
        {
            "item": item,
            "table": rows,
            # Show weight input only after user hit Check Prices
            "show_weight": bool(item),
            "graph": graph_b64,
            "max_price": max_price,
            "top_site": top_site,
            "weight": weight_raw,
            "total_value": total_value,
            "bulk_sites": BULK_WASTE_SITES,
            "scrap_sites": SCRAP_SITES,
        },
    )


#############################################
# Nearest Dump Yard logic (GoMaps / Google)
#############################################

# API key is read from Django settings or environment variable 'GOMAPS_PRO_API_KEY'
API_KEY = getattr(settings, 'GOMAPS_PRO_API_KEY', os.environ.get('GOMAPS_PRO_API_KEY', ''))

ADDRESS_VALIDATE_URL = f"https://addressvalidation.gomaps.pro/v1:validateAddress?key={API_KEY}"
PLACES_URL = "https://places.gomaps.pro/maps/api/place/nearbysearch/json"
TEXTSEARCH_URL = "https://maps.gomaps.pro/maps/api/place/textsearch/json"
FINDPLACE_URL = "https://maps.gomaps.pro/maps/api/place/findplacefromtext/json"
GEOLOCATE_URL = f"https://www.gomaps.pro/geolocation/v1/geolocate?key={API_KEY}"
DISTANCE_URL = "https://maps.gomaps.pro/maps/api/distancematrix/json"


def nearest_dump(request):
    """Landing page with the map UI."""
    js_key = getattr(settings, 'GOOGLE_MAPS_JS_KEY', '')
    return render(request, 'myapp/nearest_dump.html', {"GOOGLE_MAPS_JS_KEY": js_key})


def _address_to_latlng(address: str):
    """Resolve a free-text address to (lat, lng) with fallbacks.

    1) Address Validation API (preferred)
    2) Places Text Search (fallback)
    """
    # Normalize: trim and collapse internal spaces (do NOT lowercase to keep fidelity)
    address = " ".join((address or "").strip().split())
    # If no country given, bias to India for better results
    if address and 'india' not in address.lower():
        address = f"{address}, India"
    if not address:
        return None, None


def _address_to_latlng_with_debug(address: str):
    """Same as _address_to_latlng but returns (lat, lng, debug_dict)."""
    debug = {"normalized": None, "attempts": []}
    norm = " ".join((address or "").strip().split())
    if norm and 'india' not in norm.lower():
        norm = f"{norm}, India"
    debug["normalized"] = norm
    if not norm:
        return None, None, debug

    # 1) Address Validation
    payload = {"address": {"regionCode": "IN", "addressLines": [norm]}}
    try:
        r = requests.post(ADDRESS_VALIDATE_URL, json=payload, timeout=20)
        j = r.json()
        debug["attempts"].append({"type": "addressvalidation", "status": r.status_code, "body_status": j.get("result", {}).get("verdict", {}).get("addressComplete", None)})
        lat = j.get("result", {}).get("geocode", {}).get("location", {}).get("latitude")
        lng = j.get("result", {}).get("geocode", {}).get("location", {}).get("longitude")
        if lat is not None and lng is not None:
            return lat, lng, debug
    except Exception as e:
        debug["attempts"].append({"type": "addressvalidation", "error": str(e)})

    # 2) Text Search
    try:
        params = {"query": norm, "region": "in", "key": API_KEY}
        r = requests.get(TEXTSEARCH_URL, params=params, timeout=20)
        j = r.json()
        debug["attempts"].append({"type": "textsearch", "status": r.status_code, "api_status": j.get("status"), "error_message": j.get("error_message")})
        results = j.get("results") or []
        if results:
            loc = results[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng"), debug
    except Exception as e:
        debug["attempts"].append({"type": "textsearch", "error": str(e)})

    # 3) Find Place from Text
    try:
        params = {"input": norm, "inputtype": "textquery", "fields": "geometry", "region": "in", "key": API_KEY}
        r = requests.get(FINDPLACE_URL, params=params, timeout=20)
        j = r.json()
        debug["attempts"].append({"type": "findplace", "status": r.status_code, "api_status": j.get("status"), "error_message": j.get("error_message")})
        candidates = j.get("candidates") or []
        if candidates:
            loc = candidates[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng"), debug
    except Exception as e:
        debug["attempts"].append({"type": "findplace", "error": str(e)})

    return None, None, debug

    # Try Address Validation first
    payload = {
        "address": {
            "regionCode": "IN",
            "addressLines": [address]
        }
    }
    try:
        res = requests.post(ADDRESS_VALIDATE_URL, json=payload, timeout=20)
        data = res.json()
        lat = data["result"]["geocode"]["location"]["latitude"]
        lng = data["result"]["geocode"]["location"]["longitude"]
        if lat is not None and lng is not None:
            return lat, lng
    except Exception:
        pass

    # Fallback: Places Text Search
    try:
        params = {"query": address, "region": "in", "key": API_KEY}
        data = requests.get(TEXTSEARCH_URL, params=params, timeout=20).json()
        results = data.get("results") or []
        if results:
            loc = results[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng")
    except Exception:
        pass

    # Fallback #2: Find Place from Text
    try:
        params = {
            "input": address,
            "inputtype": "textquery",
            "fields": "geometry",
            "region": "in",
            "key": API_KEY,
        }
        data = requests.get(FINDPLACE_URL, params=params, timeout=20).json()
        candidates = data.get("candidates") or []
        if candidates:
            loc = candidates[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng")
    except Exception:
        pass

    return None, None


def _geolocate():
    """Use GoMaps Geolocation API to estimate device/server location.
    Returns (lat, lng) or (None, None).
    """
    try:
        res = requests.post(GEOLOCATE_URL, json={}, timeout=15)
        data = res.json()
        loc = (data.get("location") or {})
        lat = loc.get("lat") or loc.get("latitude")
        lng = loc.get("lng") or loc.get("longitude")
        if lat is not None and lng is not None:
            return lat, lng
    except Exception:
        pass
    return None, None


def _places_nearby(lat: float, lng: float, radius_m=15000):
    """Find waste centers / dump yards near a coordinate."""
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "keyword": "waste management center|dump yard|dumping site|landfill|garbage depot|recycling center|recycle|municipal waste|solid waste",
        "key": API_KEY,
    }
    res = requests.get(PLACES_URL, params=params, timeout=20)
    return res.json().get("results", [])


def _geocode_city_area(city: str | None, area: str | None):
    """Two-stage geocoding: resolve city first, then refine with area using location bias.

    Returns (lat, lng, dbg) where dbg describes steps taken.
    """
    dbg = {"city": city, "area": area, "steps": []}
    city = _normalize_place_text(city or "")
    area = _normalize_place_text(area or "")
    if not city and not area:
        return None, None, dbg

    # 1) Resolve city center (prefer Text Search; fallback to Address Validation)
    city_query = city or area
    if city_query and 'india' not in city_query.lower():
        city_query = f"{city_query}, India"
    city_lat = city_lng = None
    try:
        params = {"query": city_query, "region": "in", "key": API_KEY}
        r = requests.get(TEXTSEARCH_URL, params=params, timeout=15)
        j = r.json()
        dbg["steps"].append({"type": "city_textsearch", "status": r.status_code, "api_status": j.get("status"), "error_message": j.get("error_message")})
        results = j.get("results") or []
        if results:
            city_loc = results[0]["geometry"]["location"]
            city_lat, city_lng = city_loc.get("lat"), city_loc.get("lng")
    except Exception as e:
        dbg["steps"].append({"type": "city_textsearch", "error": str(e)})

    if city_lat is None or city_lng is None:
        # Fallback: Address Validation for city
        try:
            payload = {"address": {"regionCode": "IN", "addressLines": [city_query]}}
            r = requests.post(ADDRESS_VALIDATE_URL, json=payload, timeout=15)
            j = r.json()
            dbg["steps"].append({"type": "city_addressvalidation", "status": r.status_code})
            city_lat = j.get("result", {}).get("geocode", {}).get("location", {}).get("latitude")
            city_lng = j.get("result", {}).get("geocode", {}).get("location", {}).get("longitude")
        except Exception as e:
            dbg["steps"].append({"type": "city_addressvalidation", "error": str(e)})

    if city_lat is None or city_lng is None:
        return None, None, dbg

    # If no area provided, return city center
    if not area:
        return city_lat, city_lng, dbg

    # 2) Refine with area: try Find Place + bias, then Address Validation of 'area, city, India'
    # Try multiple variants for area text
    area_variants = [area]
    if area and " " not in area:
        # Try inserting a space between letters and words: 'LJUniversity' -> 'LJ University'
        import re
        area_variants.append(re.sub(r"(?<=\D)(?=University|College|Institute|Technology|School)", " ", area, flags=re.I))
    for av in [v for v in area_variants if v]:
        try:
            params = {
                "input": av,
                "inputtype": "textquery",
                "fields": "geometry",
                "locationbias": f"circle:30000@{city_lat},{city_lng}",
                "region": "in",
                "key": API_KEY,
            }
            r = requests.get(FINDPLACE_URL, params=params, timeout=15)
            j = r.json()
            dbg["steps"].append({"type": "area_findplace", "query": av, "status": r.status_code, "api_status": j.get("status"), "error_message": j.get("error_message")})
            cands = j.get("candidates") or []
            if cands:
                loc = cands[0]["geometry"]["location"]
                return loc.get("lat"), loc.get("lng"), dbg
        except Exception as e:
            dbg["steps"].append({"type": "area_findplace", "query": av, "error": str(e)})

    # Address Validation with combined area+city
    try:
        full_line = f"{area}, {city}" if city else area
        payload = {"address": {"regionCode": "IN", "addressLines": [full_line]}}
        r = requests.post(ADDRESS_VALIDATE_URL, json=payload, timeout=15)
        j = r.json()
        dbg["steps"].append({"type": "area_addressvalidation", "status": r.status_code})
        lat = j.get("result", {}).get("geocode", {}).get("location", {}).get("latitude")
        lng = j.get("result", {}).get("geocode", {}).get("location", {}).get("longitude")
        if lat is not None and lng is not None:
            return lat, lng, dbg
    except Exception as e:
        dbg["steps"].append({"type": "area_addressvalidation", "error": str(e)})

    # 3) Fallback: Text Search with combined 'area, city' and location/radius bias
    try:
        q = f"{area}, {city}" if city else area
        params = {
            "query": q,
            "location": f"{city_lat},{city_lng}",
            "radius": 40000,
            "region": "in",
            "key": API_KEY,
        }
        r = requests.get(TEXTSEARCH_URL, params=params, timeout=15)
        j = r.json()
        dbg["steps"].append({"type": "area_textsearch_bias", "query": q, "status": r.status_code, "api_status": j.get("status"), "error_message": j.get("error_message")})
        results = j.get("results") or []
        if results:
            loc = results[0]["geometry"]["location"]
            return loc.get("lat"), loc.get("lng"), dbg
    except Exception as e:
        dbg["steps"].append({"type": "area_textsearch_bias", "error": str(e)})

    # fallback: return city location if area not found
    return city_lat, city_lng, dbg


def _distance_matrix(origin_lat, origin_lng, places):
    """Call Distance Matrix for multiple destinations and combine results."""
    if not places:
        return []

    origins = f"{origin_lat},{origin_lng}"
    destinations = "|".join(
        f"{p['geometry']['location']['lat']},{p['geometry']['location']['lng']}"
        for p in places
    )

    params = {
        "origins": origins,
        "destinations": destinations,
        "key": API_KEY,
    }
    data = requests.get(DISTANCE_URL, params=params, timeout=20).json()

    # Pair places with distance/duration
    paired = []
    elements = (data.get("rows") or [{}])[0].get("elements") or []
    for idx, el in enumerate(elements):
        if el.get("status") == "OK":
            p = places[idx]
            paired.append({
                "name": p.get("name"),
                "address": p.get("vicinity", ""),
                "lat": p["geometry"]["location"]["lat"],
                "lng": p["geometry"]["location"]["lng"],
                "distance_text": el["distance"]["text"],
                "distance_value": el["distance"]["value"],  # meters (for sort)
                "duration_text": el["duration"]["text"],
                "duration_value": el["duration"]["value"],  # seconds (for sort)
                "place_id": p.get("place_id"),
                "rating": p.get("rating"),
            })
    # Sort by travel time, then distance as tiebreaker
    paired.sort(key=lambda x: (x["duration_value"], x["distance_value"]))
    return paired


def find_dumpyards(request):
    """
    API endpoint (JSON):
    - Input (query): address=... OR lat=...&lng=...
    - Output: origin + sorted list of places with distance/duration; nearest highlighted.
    """
    address = request.GET.get("address")
    lat = request.GET.get("lat")
    lng = request.GET.get("lng")
    city = request.GET.get("city")
    area = request.GET.get("area")

    # Resolve input to coordinates
    if address:
        # normalize & geocode with debug
        lat, lng, geo_dbg = _address_to_latlng_with_debug(address)
    elif (city or area) and not (lat and lng):
        lat, lng, geo_dbg = _geocode_city_area(city, area)

    if not (lat and lng):
        # As a last resort, try geolocate (approx origin)
        g_lat, g_lng = _geolocate()
        if g_lat and g_lng:
            lat, lng = g_lat, g_lng
        else:
            hint = "Ensure GOMAPS_PRO_API_KEY is set and valid. Try a fuller address like 'LJ University, Ahmedabad'."
            resp = {"error": "Please provide a valid address or lat/lng.", "address_tried": address or "", "city": city or "", "area": area or "", "hint": hint}
            if address:
                # attach debug for geocoding attempts
                resp["geocode_debug"] = geo_dbg
            elif (city or area):
                resp["geocode_debug"] = geo_dbg
            return JsonResponse(resp, status=400)

    try:
        lat_f, lng_f = float(str(lat).strip()), float(str(lng).strip())
    except ValueError:
        return JsonResponse({"error": "Invalid coordinates."}, status=400)

    # Find candidates, then compute travel time/distances
    candidates = _places_nearby(lat_f, lng_f)
    if not candidates:
        return JsonResponse({"origin": {"lat": lat_f, "lng": lng_f}, "places": []})

    ranked = _distance_matrix(lat_f, lng_f, candidates)
    return JsonResponse({
        "origin": {"lat": lat_f, "lng": lng_f},
        "places": ranked,
        "nearest": ranked[0] if ranked else None
    })


def previous_data(request):
    return render(request, 'myapp/previous_data.html')


def about(request):
    return render(request, 'myapp/about.html')


def contact(request):
    context = {}
    if request.method == 'POST':
        form_type = (request.POST.get('form_type') or '').strip()
        try:
            if form_type == 'contact':
                name = (request.POST.get('name') or '').strip()
                email = (request.POST.get('email') or '').strip()
                message = (request.POST.get('message') or '').strip()
                if not (name and email and message):
                    context['contact_error'] = 'Please fill all required fields.'
                else:
                    ContactMessage.objects.create(
                        name=name, email=email, message=message
                    )
                    context['contact_success'] = True
            elif form_type == 'feedback':
                name = (request.POST.get('name') or '').strip()
                email = (request.POST.get('email') or '').strip()
                rating_str = (request.POST.get('rating') or '').strip()
                comments = (request.POST.get('comments') or '').strip()
                try:
                    rating = int(rating_str)
                except Exception:
                    rating = 0
                if not name or rating not in [1,2,3,4,5]:
                    context['feedback_error'] = 'Please provide your name and a rating between 1 and 5.'
                else:
                    Feedback.objects.create(
                        name=name, email=email, rating=rating, comments=comments
                    )
                    context['feedback_success'] = True
        except Exception as e:
            # Generic error capture for DB issues
            context['form_error'] = f"Submission failed: {e}"
    return render(request, 'myapp/contact.html', context)


def faq(request):
    return render(request, 'myapp/faq.html')
