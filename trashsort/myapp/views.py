from django.shortcuts import render
from django.conf import settings
import os
import uuid
import requests
from PIL import Image, ImageDraw, ImageFont


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

                # Generate solutions using Gemini based on detected class
                api_key = getattr(settings, 'GEMINI_API_KEY', '')
                if api_key:
                    try:
                        prompt = (
                            "You are an expert in waste management. Material: '" + class_name + "'. "
                            "Output plain text ONLY (no Markdown, no asterisks, no bold). "
                            "Structure EXACTLY as follows:\n"
                            "Best Action: <one of Reduce/Reuse/Recycle/Responsible Disposal>\n"
                            "How to <Best Action>:\n"
                            "- 2 to 4 short, concrete steps\n"
                            "Other Suggestions:\n"
                            "- 3 to 6 short, practical tips\n"
                            "Keep it concise and suitable for the general public."
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

                                i = 0
                                n = len(raw_lines)
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
                                    if item.startswith('- '):
                                        item = item[2:].strip()
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
                                    if item:
                                        others.append(item)
                                    i += 1

                                # Fallback if model didn't follow exact structure
                                if not best_action and raw_lines:
                                    best_action = raw_lines[0]

                                context['best_action'] = best_action
                                context['best_action_details'] = how_to
                                context['other_suggestions'] = others
                                # Keep a plain text fallback for legacy template rendering
                                context['solutions_text'] = cleaned
                            else:
                                context['solutions_text'] = "No suggestions available at the moment."
                        else:
                            context['solutions_text'] = (
                                f"Suggestion service error (HTTP {resp.status_code})."
                            )
                    except Exception as e:
                        context['solutions_text'] = f"Suggestion service failed: {e}"
                else:
                    context['solutions_text'] = (
                        "Set GEMINI_API_KEY in environment to enable solution tips."
                    )
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
