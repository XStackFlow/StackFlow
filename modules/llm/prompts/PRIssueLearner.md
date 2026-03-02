# Task: Learn from PR Feedback & Fix Attempts

You have just processed a set of feedback (build errors, linting issues, or user comments) in a PR. While the fix may not be 100% successful yet, you have replied to all errors and acted on all comments. Now, reflect on these actions and update your internal memory to improve your future performance and avoid repeating the same patterns.

### Context
**Repository**: `{{repo_name}}`

### PR Feedback Addressed
{{formatted_pr_feedback}}

### Instructions
1. **Analyze the Fix**:
    - Use `git_diff()` to see only the LATEST uncommitted changes (the last fix attempt you just performed).
    - Use `git_diff(target='main')` (or `target='master'`) to see ALL changes made in this branch compared to the default branch. This represents the total work done.
2. **Determine Evolution Path**: Choose one of the following paths:
    - **Option 1**: Record to global memory if the fix represents a general coding concept, standard practice, or common LLM trap applicable across many projects.
    - **Option 2**: Record to repo memory if the fix is specific to the architectural patterns, conventions, or common errors of this specific repository.
    - **Option 3**: Do nothing (drop) if the fix is trivial, one-off, or has no long-term value for memory.
3. **Update Memory**: If choosing Option 1 or 2:
    - Use `query_memory` to check if similar learnings already exist.
    - Synthesize the new learning concisely. For example: 'When fixing X, remember that Y causes Z'.
    - Use `record_memory(content=<your_insight>, scope='global' or 'repo', category=<file_name>, repo_name=<repo_name>)` to save the learning.
      - For Option 1 (general): use `scope='global'` and an appropriate `category` like 'lessons', 'architecture', etc.
      - For Option 2 (repo-specific): use `scope='repo'`, set `repo_name='{{repo_name}}'`, and use an appropriate `category` like 'lessons', 'patterns', etc.

### Output Format

Once you have updated the memory (or decided to drop), output a JSON object:
```json
{
  "learned": true,
  "decision": "general_memory | repo_memory | dropped",
  "reasoning": "Brief explanation of why you updated/dropped memory"
}
```