import re

from radarclient.client import RadarClient 
from radarclient.model  import Query  
from interlinked_local import AI
from interlinked_local import Section
from code_search_tool import search_codebase

client = RadarClient()

def extract_code_keywords(text: str) -> list[str]:
    filenames = re.findall(r'\b[\w\-/]+\.cpp\b', text)
    functions = re.findall(r'\b[\w]+\(\)', text)
    return filenames + functions

def generate_ai_summary(user_query: str, radar, discussion_text: str, code_refs: list[str]) -> str:
    prompt = f"""
You are helping debug a firmware issue.

User query:
\"\"\"{user_query}\"\"\"

Matching Radar:
- ID: {radar.id}
- Title: {radar.title}
- Description: {radar.description}

Discussion:
{discussion_text}

Relevant code keywords from radar content:
{code_refs}

Please summarize:
- What was the problem?
- How was it resolved?
- Which code files or functions might be relevant?
- If nothing helps, suggest next steps.
"""
    return AI.chat(prompt)

def find_similar_radar_issues(user_query: str):
    component_name = "AP FW Diags B788"
    query = RadarQuery(component=component_name)
    radars = client.find(query)

    if not radars:
        return "No related Radar found in AP FW Diags B788."

    matches = []
    for radar in radars:
        full_text = f"{radar.title or ''}\n{radar.description or ''}"
        score = AI.similarity(user_query, full_text)
        matches.append((score, radar))

    matches.sort(reverse=True, key=lambda x: x[0])
    top_matches = matches[:3]

    if not top_matches or top_matches[0][0] < 0.3:
        return "No relevant Radar matches found for your failure report."

    best_score, best_radar = top_matches[0]
    discussion_entries = client.get_discussion(best_radar.id)
    discussion_text = "\n".join([e.message for e in discussion_entries]) if discussion_entries else "No discussion found."

    # 提取关键词进行代码搜索
    keywords = extract_code_keywords(f"{best_radar.title}\n{best_radar.description}")
    code_results = []
    for kw in keywords:
        code_results += search_codebase(kw)

    ai_summary = generate_ai_summary(user_query, best_radar, discussion_text, keywords)

    return (
        f"✅ Best Radar Match (Score: {best_score:.2f}):\n"
        f"- ID: {best_radar.id}\n"
        f"- Title: {best_radar.title}\n"
        f"- Description: {best_radar.description}\n\n"
        f"📚 AI Summary:\n{ai_summary}\n\n"
        f"🔎 Related Codebase Hits:\n" +
        ("\n".join(code_results) if code_results else "No matches found in source code.")
    )