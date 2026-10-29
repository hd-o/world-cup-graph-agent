# World Cup Graph Agent — Product Features

A chatbot that answers FIFA World Cup questions from its football
knowledge base. It can research missing information and add it to that
knowledge base after the user separately approves paid research and the
proposed changes.

It is an **open-source portfolio and tutorial project** for teaching
through public articles and YouTube tutorials. It is in development,
not a production service.

## Scope

- **Tournament:** FIFA World Cup only, across editions and competitions.
- **Initial coverage:** the 2026 FIFA World Cup.
- **Enrichment:** add supported World Cup information or fill missing facts.
- **Conversation:** one assistant for questions, approval requests, and
  final answers.

Supported information is not necessarily complete. Adding an edition
does not imply that all its squads, matches, or results are available.
Questions outside the supported World Cup information are outside the
enrichment scope.

## Questions and answers

The assistant supports direct lookups and questions that connect teams,
players, squads, clubs, groups, matches, goals, and venues. It can derive
statistics and comparisons from available facts.

Examples include:

- "What is the FIFA code for France?"
- "Which teams share a group with Brazil?"
- "List the strikers in the France squad."
- "Which clubs do the players in this squad play for?"
- "Who won the 2026 World Cup?"
- "Who scored the most goals?"
- "What is Mbappe's birthplace?"
- "Which match had the highest temperature in the 2026 World Cup?"
- "Which birthplace has the most players in the 2026 World Cup?"
- "Add the 2022 FIFA World Cup edition."

Striker and forward refer to the same supported position category.
Match winners and the World Cup champion are determined from recorded
results, including penalty shootouts where applicable.

### Grounded answers

The assistant is designed to check the knowledge base for factual
answers and answer only from successfully retrieved facts. Research
findings alone are not evidence for a final football answer.

A missing result is not proof that a real-world fact is zero or false.
An unsuccessful lookup is not treated as an empty factual result.
After adding information, the assistant is designed to check the
knowledge base again before answering the original question.

Lookups can return limited results. The assistant can narrow a search or
review a larger result set in parts; a limited result is not a complete
list.

## Supported World Cup information

### Editions and competitions

An edition is one occurrence of the FIFA World Cup, such as 2026. A
competition is a component of an edition, such as the finals. Groups,
squads, and matches are associated with their competition.

Enrichment can add editions and competitions within the FIFA World Cup.

### Teams, squads, players, and clubs

- Team names and FIFA codes.
- Competition participation and group membership.
- Squad membership, position, and shirt number for a particular
  competition.
- Goalkeeper, defender, midfielder, and forward position categories.
- Player names, birth dates, and birthplaces.
- Club affiliations.

Squad membership, position, and shirt number are competition-specific;
they do not establish a player's squad details for another competition.

### Matches and results

- Participating teams, dates, kickoff times, rounds, and knockout status.
- Match scores, including half-time, full-time, extra-time, and penalty
  shootout results when available.
- Match winners, draws, tournament progress, and the finals champion.
- Match-time temperature in Celsius.

The two listed teams are not designated as home and away teams. Match
totals include extra time but exclude penalty shootout scores.

### Goals

- Scorers, goal minutes, and stoppage time.
- Penalty kicks during play and own goals.
- The team credited with a goal and the team that conceded it.

A scorer's recorded name remains available even when the scorer is not
a known squad player. Own goals distinguish the scoring player from the
team receiving the goal. Penalty shootout kicks are separate from goals
scored during play.

### Venues

- Venue names and host cities.
- Country codes and capacities when available.
- Matches played at each venue.

## Enrichment with separate approvals

When a question needs missing supported information, the assistant can
propose research. Asking a question or requesting enrichment does not
authorize paid research or changes to the knowledge base.

The intended workflow is:

1. Check the existing information and identify the gap.
2. Present a focused research question and why it is needed.
3. Wait for the user's approval before paid web research.
4. Use the findings to propose specific changes with supporting evidence
   and a risk description.
5. Wait for a separate approval before applying those changes.
6. Check the updated knowledge base and answer the original question.

### Approval choices

Each approval request offers only:

- **Approve:** authorize the exact research or changes shown.
- **Reject:** skip that operation and end the enrichment attempt.

There is no Edit action on an approval request. Free-text chat is not
an approval decision. Approval requests have no automatic expiry.

Research approval covers only the displayed question, not the resulting
changes. A change proposal includes the proposed values, reasons,
reported evidence, source links, and an overall risk description. Risk
descriptions do not approve or reject a proposal automatically.

Rejecting research prevents spending on that research. Rejecting a
change proposal leaves it unapplied; it does not undo the cost of
research already approved.

### Research results

Research findings include an answer, an explanation, and reported
sources with links, titles, and excerpts. Unanswered questions are
identified rather than filled with guesses. If research produces no
usable findings, the assistant can request another separately approved
research attempt or report that the question remains unanswered.

Usable findings are reused for the change proposal rather than
repeatedly researching the same gap before applying it.

### Safe changes

Enrichment can add supported information, connect existing information,
and fill missing details. It cannot replace or delete existing information.

An approved proposal is applied as a whole: if any change fails, none of
the proposal is kept. Attempts to add duplicates or fill details that are
already present fail, even when the proposed value is the same.

The assistant reports whether enrichment added information, made no
change, was rejected, or failed. A failed proposal is not automatically
repaired or reapplied.

## Conversation continuity

Conversations continue across turns. An enrichment attempt can pause for
research approval, resume, pause for change approval, and resume again in
the same conversation.

Conversations have no automatic expiry.

### Public demo identities

The project includes two public tutorial identities, Alice and Bob, to
demonstrate private conversations. Each identity can access only its own
conversations, history, and approval requests.

Conversation creation, chat, history, and approval responses are
supported. Conversation deletion, cancellation, editing, copying,
branching, and replay are outside scope. Approval responses are not
guaranteed to be single-use. Agent Chat provides a graphical interface for
text questions, conversation history, and approval decisions. File uploads
are outside scope.

These demo identities illustrate the access boundary; they are not
production authentication. The football knowledge base is shared, so
enrichment approved by either identity is visible to both.

## Current limitations

- Coverage depends on the information currently available; support for
  a fact type does not guarantee that fact is present.
- Sources are reported by the research assistant, not independently
  verified citations. A source link or excerpt is not proof of accuracy.
- Research and ordinary chat can incur costs. Research approval is not
  an exact price guarantee, and there is no in-app budget manager.
- The intended research-before-changes sequence and post-change check
  depend on assistant behavior and can be missed. Each paid research
  operation and each change proposal still requires its own approval.
- Avoiding repeated research depends on the conversation still
  remembering earlier findings. Another research request can require
  approval if that memory is unavailable.

## Non-goals

- Tournaments other than the FIFA World Cup.
- New categories of football information beyond the supported scope.
- Paid web research or knowledge-base changes without explicit approval.
- Editing a pending operation directly on its approval request.
- Overwriting or deleting existing information.
- Automatic repair of stale proposals.
- Production identity management, operator roles, or tenant-isolated
  football knowledge bases.
- Production-service guarantees.
