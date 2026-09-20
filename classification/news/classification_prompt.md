Core ML/AI terminology

Your job is to interpret a News article in the format:
--------------
<Company Name>
<News Title>
--------------
 
Your goal is to make 2 Simple classifcation decisions.

1. company_relevant : [0,1]
Does the <News Title> clearly indicate the News Article is about the <Company Name>

2. ai_associated : [0,1,2,3]
Determine if the <News Title> is associated with the broad concept of Artifical Intelligence and if so, which category it falls into. Most cases will not be relevant to AI.


# 0 - No relation to any topics.
- Most entries should land in this category
- 
- Anything that is entirely irrelevant to this conceptual space.
Important examples to rule out:
- "Automation" alone — often NOT AI (e.g. RPA, workflow automation) unless AI-attributed
- "Algorithm" alone — too generic, usually not AI-specific
- "Smart"/"intelligent" as a marketing adjective 
- "Digital" — too broad, not AI-specific
- References to Nvidia, Micron, maths, packages and computer chips do not always guarantee AI relevancy, ensure these cases are linked to the AI trend.

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

# Examples:

0 - Facebook deleted photo albums uploaded with Adobe Lightroom

0 - CHIPSEC, by Intel – Platform Security Assessment Framework

1 - Adobe Sensei – Unified artificial intelligence and machine learning

1 - EDiffi, Nvidia's new State-of-the-Art Diffusion model

2 - The $10k Nvidia chip powering the race for AI

3 - Hugging Face raises $235M from investors including Salesforce and Nvidia