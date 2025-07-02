import json
import polib
import subprocess
import os

def get_changed_lines(file_path):
    result = subprocess.run(
        ["git", "diff", "--unified=0", file_path],
        capture_output=True, text=True
    )
    lines = result.stdout.splitlines()
    changes = {}
    current_line = None

    for line in lines:
        if line.startswith("@@"):
            parts = line.split()
            for part in parts:
                if part.startswith("+"):
                    # e.g., "+12" or "+12,3"
                    pos = part[1:]
                    if ',' in pos:
                        start, _ = pos.split(",")
                    else:
                        start = pos
                    current_line = int(start) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            if current_line is not None:
                changes[current_line] = line[1:].strip()
                current_line += 1
    return changes

def post_suggestion(repo, pr_number, file_path, line_number, suggestion):
    suggestion_block = f"""```suggestion
{suggestion}
```"""
    subprocess.run([
        "gh", "pr", "comment", str(pr_number),
        "--repo", repo,
        "--body", suggestion_block,
        "--path", file_path,
        "--line", str(line_number),
        "--body-file", "-"
    ], input=suggestion_block, text=True)

def main():
    with open("glossary.json", encoding="utf-8") as f:
        glossary = json.load(f)

    pr_number = os.environ["PR_NUMBER"]
    repo = os.environ["REPO"]
    error_found = False

    for root, _, files in os.walk("."):
        for file in files:
            if file.endswith(".po"):
                path = os.path.join(root, file)
                rel_path = os.path.relpath(path, ".")
                changed_lines = get_changed_lines(path)

                po = polib.pofile(path)
                for i, entry in enumerate(po):
                    if not entry.msgstr:
                        continue
                    for term in glossary:
                        en = term["term"]
                        correct = term["correct"]
                        wrongs = term.get("incorrect", [])
                        if en in entry.msgid:
                            msgstr = entry.msgstr
                            for wrong in wrongs:
                                if wrong in msgstr and correct not in msgstr:
                                    # 嘗試找到是哪一行包含錯誤翻譯
                                    for lineno, content in changed_lines.items():
                                        if wrong in content:
                                            print(f"📝 建議修正：{rel_path}:{lineno + 1}")
                                            post_suggestion(
                                                repo,
                                                pr_number,
                                                rel_path,
                                                lineno + 1,
                                                content.replace(wrong, correct)
                                            )
                                            error_found = True
                                            break

    if error_found:
        print("❌ 建議已送出，請修正翻譯。")
        exit(1)
    else:
        print("✅ 所有翻譯符合術語表。")

if __name__ == "__main__":
    main()