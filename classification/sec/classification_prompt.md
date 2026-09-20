Your job is to interpret a Snippet from a company's SEC filing and classify it :
------------------
SEC Filing Extract
------------------
 
Your goal is to make 1 Simple classifcation decisions.

1. ai_associated : [0,1,2,3]
Determine if the <SEC Filing Extract> is associated with the broad concept of Artifical Intelligence.
If it is not score it a 0 (The majority of examples will fall here).
If it does score it with a related category [1,2,3] (definitions below.)

<!-- DEFINITIONS -->
# 0 - No relation to an AI topics.
- Most entries should land in this category
- Anything that is irrelevant to the conceptual space.
Important examples to rule out:
- "Automation" alone — often NOT AI (e.g. RPA, workflow automation) unless AI-attributed
- "Algorithm" alone — too generic, usually not AI-specific
- "Smart"/"intelligent" as a marketing adjective 
- "Digital" — too broad, not AI-specific
- References to Nvidia, Micron, maths, packages and computer chips do not always guarantee AI relevancy, ensure these cases are linked to the AI trend.
- No specificity, boiler plate references to computing power, AI systems etc that would not allow a reader to assess materiality, imply future materiality.
- Vague references to 'autonomous','digitisation','cloud','tech' are not relevant.

# 1 - AI - Artificial Intelligence generally:
- Generative AI / GenAI 
- Natural Language Processing (NLP)
- Computer Vision
- Reinforcement Learning
- Machine Learninge
- Foundation Model(s)
- Transformer(s)
- Chatbot / Conversational AI
- Copilot / AI Assistant
- Predictive Analytics (when explicitly - AI-attributed, not generic BI)
- Infra/compute-side (relevant given your universe - includes NVDA, INTC, DELL, MU)

# 2 - AI Infrastructure
- GPU / AI accelerator / AI chip
- Data center AI / AI infrastructure
- AI cluster / AI compute capacity
- Inference / training (workload)
- Edge AI / Memory Chips/ DRAM

# 3 - AI Platforms
Named platforms/products (This should be a fallback category where the above do not apply directly)
- ChatGPT, GPT models, Claude models, OpenAI, Anthropic, Perplexity, Gemini, Copilot, Llama, MetaAI
- AWS Bedrock/AI, Azure AI, Vertex AI
- Adjacent/fuzzy-boundary terms (worth explicitly - deciding in/out, since they're ambiguous)

<!-- DEFINITIONS -->
----------------
# Not AI

+ Management contract or compensatory plan or arrangement.

In accordance with Item 601(b)(32)(ii) of Regulation S-K and SEC Release Nos. 33-8238 and 34-47986, Final Rule: Management's Reports on Internal Control Over Financial Reporting and Certification of Disclosure in Exchange Act Periodic Reports, the certifications furnished in Exhibits 32.1 and 32.2 hereto are deemed to accompany this Annual Report on Form 10-K and will not be deemed “filed” for purpose of Section 18 of the Exchange Act. Such certifications will not be deemed to be incorporated by reference into any filing under the Securities Act or the Exchange Act, except to the extent that the registrant specifically incorporates it by reference.

Pursuant to the requirements of Section 13 or 15(d) of the Securities Exchange Act of 1934, the Registrant has duly caused this report to be signed on its behalf by the undersigned, thereunto duly authorized, on February 26, 2025.

| NVIDIA Corporation | | | By: | /s/ Jen-Hsun Huang | | | Jen-Hsun Huang | | | President and Chief Executive Officer |

Power of Attorney

CHIPSEC, by Intel – Platform Security Assessment Framework

# AI
AI innovation is deeply infused into our Digital Media solutions, including through Adobe Firefly-powered generative AI features available across our Creative Cloud flagship apps, and through  AI Assistant, a generative AI-powered conversational interface designed to enhance document experiences. 

We are engaged with thousands of organizations working on AI in a multitude of industries,

enterprises that are increasingly turning to AI to improve products and services; and startups seeking to implement AI in transformative ways across multiple industries. We partnered with industry leaders such as IBM, Microsoft, Oracle, SAP, and VMware to bring AI to enterprise users. We also have partnerships in healthcare and manufacturing, among others, to accelerate the adoption of AI.

"AI agents"/"Teamwork Graph,"

"demand for AI solutions," MU 

"threat actors...using artificial intelligence,"

"adjacent products in programmable solutions, AI, and autonomous driving," ADBE "EU AI Act,

" NVDA "deep learning is a new AI computer model," ADBE "expand generative AI into all our product offerings," TEAM "AI-powered virtual teammates"

They are available in industry standard servers from every major computer maker and CSP, as well as in our DGX AI supercomputer, a purpose-built system for deep learning and GPU accelerated applications. To facilitate customer adoption, we have also built other ready-to-use system reference designs around our GPUs, including HGX for hyperscale and supercomputing data centers, EGX for enterprise and edge computing, IGX for high-precision edge AI, and AGX for autonomous machines.

Hugging Face raises $235M from investors including Salesforce and Nvidia