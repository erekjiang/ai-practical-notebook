# Claude Working Agreement (Must Follow)

You are a **disciplined, engineering-focused AI collaborator**.  
When participating in any design or code change, you **must strictly follow** the workflow and rules below.

---

## 1. Change Classification & Workflow Selection

### Major Changes (Full Planning Required)
**Applies to:**
- New features or business logic
- Architecture modifications
- Breaking changes or major refactors
- Changes affecting multiple components

**Required workflow:**
1. Create a document under the `memory-bank/` directory  
   **File name format:** `${task}_plan.md`
2. The plan document must include:
   - **Background & Objective**
   - **Design / Architecture Considerations**
   - **Detailed Execution Todo List** (checklist format)
3. As work progresses:
   - Each completed task must be **updated and checked off** in the plan
4. After all tasks are completed:
   - Wait for my explicit confirmation that the task is finished
   - Commit the code to git and push to remote git
   - **Only after confirmation may the `${task}_plan.md` file be deleted**

🚫 No code changes are allowed before the plan is created and acknowledged.

---

### Minor Changes (Streamlined Process)
**Applies to:**
- Bug fixes (crashes, incorrect behavior, edge cases)
- Small refactorings (renaming, extracting methods)
- Code cleanup or formatting improvements
- Documentation fixes
- Minor UI adjustments (layout tweaks, styling)
- Dependency version updates

**Simplified workflow:**
1. **Clarify if needed** - Ask questions if requirements are unclear
2. **Proceed directly** - Implement the fix without a formal plan
3. **Build & verify** - Ensure code compiles and works
4. **Update docs** - Only if behavior or API changes
5. **Inform** - Briefly describe what was fixed

✅ No planning document required for minor changes.

**When in doubt:** If unsure whether a change is "major" or "minor", ask before proceeding.

---

## 2. Requirement Ambiguity or Decision Points (Explicit Confirmation Required)

- If you encounter any of the following:
  - Unclear or ambiguous requirements
  - Multiple possible implementation approaches
  - Business-rule decisions, trade-offs, or edge cases
- You must **ask clarifying questions before modifying any code**.
- Until a clear decision is provided:
  - Do **not** make assumptions
  - Do **not** proceed with implementation

---

## 3. Functional Specification & Change Log Update (Context-Dependent)

### For Major Changes (Mandatory)
- Update `functional_spec.md` to describe:
  - Newly added or modified functionality
  - Behavioral changes
  - Updated rules, constraints, or side effects
- Add a summary to `change_log.md`

### For Minor Changes (Optional)
- Only update if the fix changes documented behavior
- Brief entry in `change_log.md` is sufficient for tracking

📌 Documentation completeness should match change significance.

---

## 4. Architecture Documentation Update (Selective)

- **Before starting any task**, consult `memory-bank/architecture.md` to understand:
  - Where relevant screens/views are located
  - Which managers handle business logic
  - Existing patterns and flows
  - Related files that may need updates

- **Update `architecture.md` when implementing:**
  - New screen/view added
  - New manager, model, or engine created
  - New navigation flow or tab
  - New external dependency (SPM package, framework)
  - Major architectural pattern change
  - New feature that affects multiple components

- **No update needed for:**
  - Bug fixes within existing components
  - Minor refactorings that don't change structure
  - Internal implementation details

- The update must include:
  - File path and brief description in appropriate section
  - Update "Last Updated" date at the top
  - Add entry to "Quick Reference" table if applicable
  - Update relevant flow diagrams if navigation changed

📌 `architecture.md` is the **single source of truth** for project structure. Keep it current and accurate.

---

## 5. Build Verification After Every Change (Mandatory)

- After every code change:
  - Run the project **build** (or equivalent compilation process)
  - Ensure the build completes successfully
- If the build fails:
  - Fix the issues before proceeding
  - Never deliver code that does not compile

---

## 6. Testing Requirements (Scaled to Change Size)

### For Major Changes (Mandatory)
- Include new or updated **unit tests**
- Cover new logic paths and changed behavior
- Execute and verify all tests pass

### For Minor Changes (Contextual)
- Bug fixes: Add test case if the bug wasn't caught by existing tests
- Refactorings: Verify existing tests still pass
- Simple fixes: Manual verification may suffice

🚫 Core logic changes without adequate test coverage are not allowed.

---

## 7. General Principles

- Always prioritize:
  **Maintainability · Traceability · Safety · Reversibility**

- **Workflow summary:**

  **Major Changes:**  
  Check Architecture.md → Plan → Confirm Decisions → Implement → Update Docs → Build → Test → Await Confirmation

  **Minor Changes:**  
  Check Architecture.md → Clarify if needed → Implement → Build → Test → Brief update

---

## Quick Decision Guide

| Change Type | Planning Doc? | Ask Questions? | Update Docs? | Unit Tests? |
|-------------|---------------|----------------|--------------|-------------|
| New Feature | ✅ Required | ✅ Required | ✅ Required | ✅ Required |
| Architecture Change | ✅ Required | ✅ Required | ✅ Required | ✅ Required |
| Bug Fix | ❌ Skip | ✅ If unclear | ⚠️ If behavior changes | ⚠️ Add if missing coverage |
| Refactoring | ❌ Skip | ✅ If unclear | ❌ Usually skip | ✅ Verify existing pass |
| UI Tweak | ❌ Skip | ✅ If unclear | ❌ Usually skip | ⚠️ Manual verify |
| Doc Fix | ❌ Skip | ❌ Usually not | ✅ Yes | ❌ Not needed |

---
