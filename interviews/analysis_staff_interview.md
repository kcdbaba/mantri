# Staff Interview Analysis — 2026-03-27

**Date of interview:** March 27, 2026 (11:17 AM session)
**Analysed by:** Claude Sonnet 4.6

---

## INTERVIEW SUMMARY

- **Interviewee type:** STAFF
- **Key role/context:** Two frontline staff members — Samita (or Moushmi; names are slightly garbled in transcript) and at least one other staff member (possibly named Ajay / referred to as "Ajay ko"). Both work in the Guwahati office, handling day-to-day coordination with clients, suppliers, and delivery logistics. Ashish is also briefly present in the session and contributes several clarifying remarks. This is effectively a 3-person session, not a 1-on-1.
- **Session length:** Approximately 67 minutes (00:00:00 → 01:07:14)
- **Overall signal:** Staff confirm that message overload and task slip-through are real, daily occurrences — not occasional edge cases. The single biggest surprise is that **the primary privacy concern staff expressed is not about data security or AI access, but about their personal messages being accidentally read alongside business chats** because they use their personal phones for business WhatsApp groups. This is a concrete product design constraint that was not previously foregrounded.

---

## CURRENT WORKFLOW

- **Handwritten notebook + diary as primary capture:** When a task is assigned or a message arrives, staff write it down in a personal notebook or diary.
  > "we are writing down in our notebook and diaries" [00:33:41] [original: "इट डाउन इन नोटबुक एंड डाइरीज ए टाइम सर"]
  > Translation: "We write it down in notebooks and diaries, sometimes we remember it directly."

- **Excel sheet as shared secondary tracker:** After the initial notebook entry, staff transfer tasks to a shared Excel sheet that is visible to all members. The sheet uses color coding — green = done, orange = in-progress or nearing risk, red = problem — and tasks not completed roll over to the next day.
  > "using an Excel sheet and which are words is done with a color sort like that... task is done we do not look orange red and if there's a problem and the next whatever is left behind is popped and brought forward to the next day as pending task from yesterday" [00:34:15–00:35:31]

- **Tasks from WhatsApp carry into the notebook then Excel; the pipeline is sequential.** The flow is: WhatsApp message arrives → staff read it → write in notebook → when free, transfer to Excel. The gap between WhatsApp and Excel is where most slippage occurs.

- **Phone calls used for quick follow-ups, but not logged back to WhatsApp or Excel:** Staff confirm that when a WhatsApp message needs a quick response, they often call the supplier/client, resolve it verbally, inform Ashish verbally ("sar ko bata diya"), and mark it done — without updating the group chat.
  > "जैसे हमें कोई मैसेज जा गए हैं जो टाट में है तो उनको वह फोन हमने कर लिया हो गया वह करके हमने सर को बता दिया तो वह डन है तो उसके लिए हम एगेंड वर्ष में जाकर उसको कुछ अलग से लिखे नहीं देते हैं" [00:51:00–00:51:18]
  > Translation: "If there's a message about something in a chat, we call them on the phone, it's done, we tell sir, it's done — so for that we don't separately go and write it anywhere again."

- **No dedicated reminder tool is used for work tasks:** Staff occasionally use phone alarm/notification apps (e.g., Google Calendar reminders) for personal events like birthdays and anniversaries, but never systematically for business task reminders.
  > "नोटिफिकेशन के लिए हम कभी डाइडी नोट्स रख देते हैं जैसे किसी का बर्डे हो गया एनविरसरी... वैसे टास्क के लिए हमने वैसे यूज नहीं किया" [00:49:34–00:49:48]
  > Translation: "For notifications we sometimes set diary notes for things like someone's birthday or anniversary... for work tasks we haven't used it that way."

- **Urgent tasks are completed before leaving office; non-urgent ones deferred to next morning:**
  > "शाम को जो भी काम है वह बहुत अर्जंट होता है तो हम करके जाते हैं अगर नहीं हो तो हम अगले दिन आकर करते हैं तो हम किसी एक्सएल में या फिर डायरी में नोट करके रख देते हैं" [00:44:40–00:44:46]
  > Translation: "Whatever work is there in the evening, if it's very urgent we do it before leaving; if not, we come the next day and do it — we note it in Excel or diary."

---

## PAIN POINTS

- **[HIGH] Message overload causes daily task slip-through.** This is the central pain. Multiple messages arrive in rapid succession across multiple groups; an important single-line task is buried.
  > "एक मैसेज आया है कि [action item]. उसके बाद बैक टू बैक नीचे में आ गया, मतलब और भी मृत्यु जिस आ जाते हैं, तो उस जो देखेंगे... डिसीटर कर लेना था वह इतना मतलब कभी एक लाइन में होता है तो वह विशों नहीं होता" [00:39:42–00:39:56]
  > Translation: "A message came saying [do this task]. Right after, back-to-back more messages came below it — the one-liner action item gets invisible because so many others arrive on top."

  > "वह ओवरशड़ो हो जाता है कि उसके बाद बेक टो बेक बहुत सारे आ जाते हैं तो हम कभी हम वह भूल जाते हैं और वही जाकर बाद में हमें हिट करता है" [00:39:03–00:39:11]
  > Translation: "It gets overshadowed, so many come back-to-back that we forget — and later that's the one that comes back to bite us."

- **[HIGH] Written-down tasks get forgotten when workload is high.** Even after noting a task, staff forget to follow through when they are context-switching rapidly.
  > "यह बहुत होता है... जो यहां वह करते-करते वह भूल जाता हूं... बाद में ओ हां सर जब बोलते हैं आशिर सब जब टेंट कि यह हो गया तब यादा ओ नहीं सर सॉरी भूल गई" [00:41:34–00:41:51]
  > Translation: "This happens a lot... while doing something else you forget what was written down... later when sir asks 'was this done?' only then you remember — 'Oh no sir, sorry, I forgot.'"

- **[HIGH] Cross-assumption problem: both staff assume the other has seen a message.** When the same message is visible to two people, both assume the other handled it, so neither does.
  > "दोनों का एज्म्शन रहता है कि उसके पास थोड़ा टाइम होगा तो वह देख लेगा... दोनों से देखा है तो आपने देख लिया है तो बता दिया करो" [00:40:37–00:40:58]
  > Translation: "Both assume the other had a bit of time and would check it... since both saw it, they assume the other handled it and reported."

- **[MEDIUM] No clear alert channel that won't itself get lost in WhatsApp noise.** When Kunal asked how to deliver reminders, staff pointed out the paradox: any alert sent via WhatsApp would face the same overload problem.
  > "अगर आप बहुत बिजी है राइट वह आने से भी बुरा नहीं है सर उसको बुलना... मतलब इतना बड़ा पेन है उसके बाद उसको रेक्टिफाई करने के लिए" [00:46:46–00:47:00]
  > Translation: "If you're very busy, not getting [the alert] isn't worse than getting it [and missing it]... the pain of the missed task and then rectifying it is huge."

- **[MEDIUM] Pressure leads to deliberate deferral that then gets forgotten.** Under load, staff consciously decide "I'll do this later / tomorrow" — but the deferred item slips.
  > "ओवर मतलब काम की वजह से यह बाद में करेंगे या फिर यह यह पहले करना है ऐसे तो बाद में जाकर उपयोगे..." [00:54:26–00:54:41]
  > Translation: "Because of work overload we say 'we'll do this later, or this has to be done first' — and then later it falls through."

- **[MEDIUM] Off-platform resolutions are not logged back.** Phone call outcomes (especially quick supplier confirmations) are rarely written back into WhatsApp or Excel. Ashish is verbally told but there is no written trail.

- **[LOW] Delivery completion reports are logged, but routine non-urgent resolutions are not.** There is a clear completion report protocol for deliveries ("we give a completion report when we deliver"), but for everyday calls and small tasks, nothing is written.

---

## QUALITY RISK VALIDATION

**Cadence implicit tasks**: VALIDATED
> "काफी सारे ग्रुप में हर एक यूनिट में अलग-अलग प्रेज होते हैं... कभी हमें रेट के लिए लेना होता है, कभी फोटो एरेंज करने होते हैं, कभी फिर डिलीवरी के लिए देखना होता है" [00:39:20–00:39:35]
> Translation: "In so many groups, each unit has different phases... sometimes we need to arrange rates, sometimes photos, sometimes follow delivery."
> Interpretation: Staff are operating multiple order phases simultaneously, each with its own set of habitual steps — none of which are written down or signalled in WhatsApp unless someone actively posts an update.

**Staff quality variance**: VALIDATED (partially, by implication)
> The cross-assumption problem described above — where two staff members each expect the other to have handled a task — implies that responsibility ownership is unclear or informal. The Excel colour-coding system suggests Ashish and management are aware enough of variance to have built a system around it, though the system breaks under load.
> [No direct quote attributing specific slippage to specific staff; staff themselves acknowledged the pattern is systemic.]
> Interpretation: The pattern of "both thought the other had it" is a textbook quality-variance signal — workload obscures who owns what.

**Missing WhatsApp entries**: VALIDATED — strong
> "जैसे हमें कोई मैसेज जा गए हैं जो टाट में है तो उनको वह फोन हमने कर लिया हो गया वह करके हमने सर को बता दिया तो वह डन है तो उसके लिए हम एगेंड वर्ष में जाकर उसको कुछ अलग से लिखे नहीं देते हैं" [00:51:00–00:51:18]
> Translation: "We phoned them, resolved it, told sir it's done — but we don't go back and separately write that anywhere."
> Interpretation: Off-platform resolutions (phone calls to suppliers/clients) are a daily pattern and are confirmed to not be logged back to WhatsApp. This is a systematic information gap that Mantri cannot close from WhatsApp alone.

**Warehouse inventory blindness**: NEUTRAL — not discussed
> No mention was made of orders being filled from existing stock, nor of any inventory system. This is likely in scope for the business but staff in this interview focused on coordination and communication, not inventory.

**Trust threshold**: VALIDATED — low threshold for agent suggestions, with clear conditions
> "नहीं सर एक रिमाइंडर अगर रहता है हमें मतलब कभी हम भूल जाते हैं तो इस बेटर ना... रिमाइंडर होने से तो बेटर है सर लाइफ इजी मच इजीयर" [00:42:36–00:42:47]
> Translation: "No sir, if there's a reminder it's better for us — sometimes we forget, so it's better, sir, life is much easier with a reminder."
> Interpretation: Staff's tolerance for agent suggestions is high IF the reminder is timely and genuinely pertinent. The trust threshold is not skepticism-based — staff want help and will act on alerts. The risk is not disbelief but overload: too many alerts will themselves get buried.

**Off-platform instruction gap**: VALIDATED — confirmed as daily occurrence
> "सर यह हमेशा यह लगभग डेग्लरली होता है... जैसे हमें काम आ गया है लेकिन हम कभी इसका फोन पर दे देते हैं... लेकिन कंप्लीशन रिपोर्ट मैक्सिमम नहीं" [00:51:27–00:51:50]
> Translation: "Sir this almost always happens daily... like when work comes, we sometimes handle it on the phone... but completion reports mostly don't happen [for small tasks]."
> Interpretation: Daily off-platform instructions and resolutions are the norm, not the exception. Mantri should be designed to handle this as a structural gap, not a rare edge case.

---

## NEXT BEST ALTERNATIVE

**What staff currently use:**
1. Personal handwritten notebook / diary — primary real-time capture
2. Shared Excel sheet (color-coded, visible to all office members) — transferred from notebook when free time allows
3. Memory — relied upon for short windows ("I'll remember this for now")
4. Ashish's verbal follow-up — Ashish occasionally asking "was this done?" is the de facto reminder system

**What bar Mantri needs to clear:**
- The Excel + notebook system works well when load is normal. It breaks under overload. Mantri therefore needs to work specifically at high-load moments — catching the things that slip when everything is busy.
- Staff already have a structured mental model of task priority (urgent vs. deferrable). Mantri's alerts need to align with this model — high-urgency same-day alerts, lower-urgency next-morning summaries — rather than creating a new parallel priority system.
- The bar is not "replace Excel" — it's "catch what Excel misses when we're overwhelmed."

---

## TRUST AND ADOPTION SIGNALS

**What would make them trust the agent:**
- Alerts that are sparse and genuinely pertinent: "दिन में दो बारी करें या फिर तीन बारी" [00:43:01] — "Do it two or three times a day, not constantly."
- Morning check-in summarising what's pending for the day: "मौनिंग में को खोल कि हम अगर देख लें कि हां यह मेरे का है" [00:43:01–00:43:07] — "In the morning when we open [the app] we can see 'this is my task for today.'"
- Not disrupting their current WhatsApp workflow: they want a complementary layer, not a new system to manage.
- "यूजफुल अगर यह एकदम से काम करना स्टार्ट हो जाए सर तो यूजफुल तो बहुत होगा सर" [00:53:27] — "If this starts working right away, it would be very useful sir."

**What would make them ignore or switch it off:**
- Constant reminders throughout the day — explicitly called out as creating more noise.
  > "यह अगर कॉंस्टेंटली आपको सारा दिन रिमाइंडर करता रहे तो आपके लिए तो और भी नॉइस आ गया" [00:42:54]
  > Translation: "If it constantly reminds you all day long, it adds even more noise."
- Alerts delivered through WhatsApp that themselves get buried in the same overloaded feed.
  > "वह रिमाइंड इमीडिटली करें तो [I would] मैंने तब्ड़ हो ओबरे देखा ठीक है चलो यह खत्म कर लेटी फिर करूंगी ऐसी आ जाता है" [00:43:17–00:43:31]
  > Translation: "If it reminds me right away, I'd see it, think 'okay let me finish this first', then [the reminder] gets buried too."

**Level of control staff need:**
- No strong demand for a correction interface was articulated directly by staff (unlike Ashish-type users). Staff mainly want to be told what to do and have the ability to confirm completion.
- Staff implicitly accepted the current design (human-in-the-loop, agent does not make decisions) without objection.
- The correction interface mentioned by Kunal and Ashish in the presentation portion was acknowledged positively but not explored in depth in staff Q&A.

---

## STAFF-SPECIFIC FINDINGS

**Privacy and comfort signals:**
This is the most important staff-specific finding. Staff raised the privacy concern spontaneously and with notable clarity:
> "वर्ड सब जैसे हम आज यह जो कोंटेक्ट नंबर है यह हमारा पर्सनल है यह पॉइंट पर्सनल मोबाइल है तो हम कभी यह अपने फैमिली मेंबर्स कभी मैसेज जाता है अपने फ्रेंड्स के भी मैसेज और आपको पता है फ्रेंड्स सरकल में मैसेज तो रहते हैं तो लाइक इसी वजह से मतलब यह ऑफिशियल और जो अपना पर्सनल मैसेज यह मिक्स होकर कोई इशू ना जाए" [00:56:22–00:56:48]
> Translation: "The contact number we use is our personal number, on a personal mobile — so family messages come there, friends' messages come there. We don't want the mixing of our official and personal messages to cause any issue."

**This is not a theoretical concern — staff use their personal phones for business WhatsApp groups.** The implication is that the AI will have access to the same device/number that receives personal messages from family and friends. Staff's worry is that personal chats could accidentally be processed or exposed.

Ashish (in presenter role) immediately addressed this concern:
> "आप सिलेक्ट करके दोगे जिस ग्रूप में आशीष है रहे उसमें तो आपको शेयर करने की जरूरत ही नहीं है विकस उसके परमिशन से हम देख लेंगे उस ग्रूप में क्या चल रहा है... बस यह मतलब हो जाए कि हम चूज कर सकते हैं तो कोई इशू नहीं होगा" [00:58:15–01:00:04]
> Translation: "You just select which groups to share — for groups Ashish is in, you don't even need to share separately. As long as we can choose, there won't be any issue."

Staff's final verdict was that the whitelist/choose-your-groups mechanism is sufficient to address their concern:
> "अदरवेज कोई हमें प्रॉब्लम नहीं इशूज नहीं है और यह रातर हमारे लिए बहुत परसिशिक होगा" [00:57:35–00:57:40]
> Translation: "Otherwise we have no problems or issues — this will in fact be very useful for us."

**Override and control needs:**
Staff did not raise a strong demand for a correction/override interface in their own words. This is consistent with their role — they execute tasks rather than manage the system. Their implied control need is:
- The ability to mark a task as done (close it out)
- The ability to see today's pending tasks clearly (without noise)

No request was made for the ability to tell the agent "this is wrong" or to re-route a task. This suggests the override UX is more important for Ashish than for frontline staff.

**Noise tolerance:**
Staff gave a very concrete and actionable answer:
> "दिन में दो बारी करें या फिर तीन बारी करर दो बदल... मौनिंग में को खोल कि हम अगर देख लें कि हां यह मेरे का है... पढ़ते बाद छाम को धन्यवाद देंगे" [00:43:01–00:43:07]
> Translation: "Do it two or three times a day — in the morning when we open [the app], see what's ours for today... and then in the evening a thank-you [i.e. a wrap-up]."

Ideal cadence per staff: **morning digest + evening wrap-up, with possibly one mid-day critical-only alert**. Not continuous. Not real-time for routine tasks. Real-time only for genuinely critical/urgent items (confirmed: "काम जो ज्यादा अर्जेंट है" ["work that is more urgent"]).

The staff member also distinguished between two alert types:
1. **Urgent (same-day, time-sensitive):** needs real-time or near-real-time alerting — but the delivery channel for these is unresolved (WhatsApp itself is noisy)
2. **Non-urgent (next-day deferrable):** morning summary is sufficient

---

## SURPRISES AND NEW HYPOTHESES

**⚠️ SURPRISE 1 — Personal phone = primary business device. Privacy risk is concrete, not hypothetical.**
Staff confirmed they use their personal mobile numbers and personal phones for business WhatsApp. This means the integration point for Mantri (WhatsApp API access) will be to devices that contain personal family and friend messages, not dedicated business phones. The whitelist mechanism is necessary but the perceived risk is real and was the first concern staff raised unprompted.
Design implication: the group selection / whitelist UX must be the most prominent, trust-establishing feature in onboarding, not a footnote.

**⚠️ SURPRISE 2 — Staff do NOT want real-time alerts as a default. Morning + evening cadence preferred.**
The assumption embedded in many alert systems is that "faster = better." Staff explicitly rejected this. They want a batched digest, not a stream. This challenges the design assumption that Mantri should alert as soon as it detects a gap.
⚠️ ASSUMPTION CHALLENGED: Do not default to real-time alerts. Default to batched (morning/evening) with escalation to real-time only for highest-urgency items.

**⚠️ SURPRISE 3 — The "two people both assumed the other handled it" failure mode is common.**
This is a coordination failure that no individual WhatsApp message will reveal. No single message says "I assumed you would do this." This pattern is structurally invisible to an agent that only reads messages. It can only be inferred by the absence of task acknowledgement over time — i.e. Mantri detecting that a task has been in-progress for too long with no update.

**⚠️ SURPRISE 4 — Staff have a working prioritization model in their heads.**
Staff articulated a clear mental model: urgent (do today before leaving), non-urgent (defer to morning). They are not operating without structure — the structure exists but breaks under overload. Mantri's task priority assignment should reinforce their existing mental model rather than introduce a new one. The existing Excel color-code (green/orange/red) is already a tested vocabulary they use.
Design implication: Use priority language that maps to their existing system. Green/orange/red may be better UI vocabulary than numeric priority scores.

**⚠️ SURPRISE 5 — Phone call resolution without WhatsApp update is confirmed as a daily norm, not an exception.**
> "यह हमेशा यह लगभग डेग्लरली होता है" [00:51:27]
> Translation: "This almost always happens daily."
This means Mantri's task state model will be systematically stale for a significant portion of tasks. The agent should be designed to tolerate unknown completion states gracefully — not flag every unconfirmed task as an overdue risk after the first few hours.

**⚠️ SURPRISE 6 — There is no prior experience with any workflow reminder tool for business tasks.**
Staff have only used reminders for personal events (birthdays, anniversaries). They have never used a business task reminder tool. This means there is no learned behaviour or muscle memory to build on — Mantri will be setting the baseline expectation. First impressions matter more than in a market where users have used alternatives.

---

## RECOMMENDED NEXT STEPS

1. **Resolve the alert delivery channel problem before building the alert content.** Staff correctly identified that WhatsApp-delivered alerts will suffer the same overload problem as WhatsApp messages. This is a design blocker: consider a separate notification surface (dedicated dashboard on office screen, a separate mobile app, or a distinct WhatsApp bot contact they can treat as high-priority) rather than injecting into the same busy group chats. This decision should be made before Sprint 3 alert design.

2. **Build the alert cadence model around morning digest + evening wrap-up, not real-time stream.** Instrument the system to escalate to immediate notification only when task priority is "critical." Define "critical" in concrete terms (e.g. delivery is due today and supplier dispatch not confirmed, or payment is overdue by X days). All other alerts should batch into morning and evening summaries. This directly matches what staff said they want and avoids the noise problem.

3. **Quantify the off-platform gap before Sprint 3 evaluation.** Staff confirmed phone-call resolutions happen daily but without WhatsApp logging. Before evaluating Mantri's task-tracking accuracy, establish a baseline: how many tasks per day are resolved entirely off-platform and never reflected in WhatsApp? Without this baseline, accuracy metrics will be misleadingly pessimistic (Mantri will appear to "miss" tasks that were simply resolved off-channel). A one-week log by staff (manual, lightweight: just tick a sheet when they make a call that resolves a task) would give this number.
