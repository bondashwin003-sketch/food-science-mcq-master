import os
import json
from pathlib import Path
from typing import List, Literal
from PIL import Image
from pdf2image import convert_from_path
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# =====================================================================
# 1. Define Structured Output Schema using Pydantic
# =====================================================================

class MCQItem(BaseModel):
    question: str = Field(description="The multiple-choice question stem.")
    options: List[str] = Field(
        description="List of 4 options labeled A, B, C, and D."
    )
    correct_answer: str = Field(
        description="The exact correct option (e.g., 'A', 'B', 'C', or 'D')."
    )
    explanation: str = Field(
        description="Detailed explanation of why the answer is correct and why distractors are wrong."
    )
    difficulty: Literal["Easy", "Medium", "Hard"] = Field(
        description="Difficulty level: Easy (recall), Medium (application), Hard (analytical/synthesis)."
    )
    topic_tag: str = Field(
        description="The specific sub-topic or concept tested by this question."
    )

class DocumentExtractionAndQuiz(BaseModel):
    extracted_text_summary: str = Field(
        description="Complete and accurate transcription/summary of the text, equations, and diagrams detected in the document."
    )
    mcqs: List[MCQItem] = Field(
        description="List of generated Multiple Choice Questions across difficulty levels."
    )

# =====================================================================
# 2. Core Document Loader (Handles Images & Scanned/Digital PDFs)
# =====================================================================

def load_document_as_images(
    file_path: str, 
    start_page: int = 1, 
    end_page: int = 5
) -> List[Image.Image]:
    """
    Loads an image or converts specific pages of a PDF into PIL Image objects.
    Passing images to Gemini Vision ensures handwritten notes, scanned pages, 
    tables, and chemical/mathematical formulas are read accurately.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    images = []

    if ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"]:
        print(f"[*] Loading image file: {path.name}")
        images.append(Image.open(path))
        
    elif ext == ".pdf":
        print(f"[*] Rendering PDF pages ({start_page} to {end_page}) into high-res images...")
        # 200 DPI provides an optimal balance between OCR accuracy and token usage
        images = convert_from_path(
            str(path),
            dpi=200,
            first_page=start_page,
            last_page=end_page
        )
        print(f"[+] Successfully loaded {len(images)} page(s) from PDF.")
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use PDF, PNG, JPG, or WEBP.")

    return images

# =====================================================================
# 3. OCR + Multi-Level MCQ Generation Engine
# =====================================================================

def extract_and_generate_mcqs(
    file_path: str,
    num_easy: int = 3,
    num_medium: int = 3,
    num_hard: int = 3,
    start_page: int = 1,
    end_page: int = 5
) -> DocumentExtractionAndQuiz:
    """
    Processes images/PDF pages using Gemini 2.5 Flash to extract content 
    (typed, scanned, or handwritten) and generate tiered MCQs.
    """
    # Initialize client (automatically picks up GEMINI_API_KEY from environment)
    client = genai.Client()

    # Load visual content
    document_images = load_document_as_images(file_path, start_page, end_page)

    # Construct the pedagogical prompt
    prompt = f"""
    You are an expert OCR system and academic examiner.
    Carefully examine the attached document page(s), which may contain typed textbook text, 
    scanned pages, tables, diagrams, or handwritten notes.

    TASK 1: CONTENT EXTRACTION
    Accurately read and transcribe the core text, formulas, definitions, and key points 
    from the provided page(s) into the `extracted_text_summary` field.

    TASK 2: TIERED MCQ GENERATION
    Based strictly on the extracted content, generate a total of {num_easy + num_medium + num_hard} 
    high-quality Multiple Choice Questions (MCQs) distributed across three difficulty levels:
    
    - {num_easy} EASY questions: Focus on direct factual recall, definitions, terminology, and basic concepts.
    - {num_medium} MEDIUM questions: Focus on conceptual understanding, cause-and-effect, mechanism comparison, and practical application.
    - {num_hard} HARD questions: Focus on multi-step analytical reasoning, numerical problem-solving (if applicable), assertion-reason style logic, or experimental scenarios.

    Ensure every question has 4 plausible options (A, B, C, D) with strong distractors, 
    an explicit correct answer, and a clear explanation.
    """

    # Combine images and prompt into the contents payload
    contents = document_images + [prompt]

    print("[*] Analyzing document content and generating MCQs via Gemini...")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DocumentExtractionAndQuiz,
            temperature=0.3, # Low temperature keeps questions strictly grounded in the text
        ),
    )

    # Parse validated Pydantic object from JSON response
    result_data = json.loads(response.text)
    return DocumentExtractionAndQuiz(**result_data)

# =====================================================================
# 4. Formatting & Export Utilities
# =====================================================================

def display_and_save_results(result: DocumentExtractionAndQuiz, output_prefix: str = "quiz_output"):
    """Prints the extracted content and MCQs to the console, and saves to JSON and Markdown."""
    
    # 1. Save raw JSON for programmatic use
    json_path = f"{output_prefix}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
    
    # 2. Build readable Markdown report
    md_lines = [
        "# Extracted Content & Generated Question Bank\n",
        "## 1. Extracted Content Summary\n",
        f"{result.extracted_text_summary}\n\n",
        "---\n",
        "## 2. Generated Multiple Choice Questions\n"
    ]

    for difficulty in ["Easy", "Medium", "Hard"]:
        tier_questions = [q for q in result.mcqs if q.difficulty == difficulty]
        if not tier_questions:
            continue
            
        md_lines.append(f"### {difficulty} Level Questions\n")
        for idx, q in enumerate(tier_questions, 1):
            md_lines.append(f"**Q{idx}. [{q.topic_tag}] {q.question}**\n")
            for opt in q.options:
                md_lines.append(f"- {opt}")
            md_lines.append(f"\n> **Correct Answer:** {q.correct_answer}  ")
            md_lines.append(f"> **Explanation:** {q.explanation}\n")

    md_path = f"{output_prefix}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    print(f"\n[+] Saved structured JSON to: {json_path}")
    print(f"[+] Saved formatted Question Bank to: {md_path}\n")
    print("\n".join(md_lines))

# =====================================================================
# 5. Execution Entry Point
# =====================================================================

if __name__ == "__main__":
    # Ensure API key is configured
    if not os.environ.get("GEMINI_API_KEY"):
        raise EnvironmentError(
            "GEMINI_API_KEY environment variable not set. "
            "Set it using: export GEMINI_API_KEY='your_key'"
        )

    # Replace with the path to your handwritten note image, scanned page, or textbook PDF
    SAMPLE_FILE = "sample_textbook_chapter.pdf" 

    # Example configuration
    try:
        quiz_data = extract_and_generate_mcqs(
            file_path=SAMPLE_FILE,
            num_easy=3,
            num_medium=4,
            num_hard=3,
            start_page=1,  # Start page if input is a PDF
            end_page=3     # End page if input is a PDF
        )
        display_and_save_results(quiz_data, output_prefix="chapter_1_mcqs")
        
    except FileNotFoundError as e:
        print(f"[!] Error: {e}")
        print("[i] Update `SAMPLE_FILE` with a valid path to an image (.png, .jpg) or .pdf file.")
    except Exception as e:
        print(f"[!] An unexpected error occurred: {e}")