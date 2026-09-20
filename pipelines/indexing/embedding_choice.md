Model	Free?	NDCG@10 (finance retrieval)
Fin-E5 (finance-tuned, open)	Yes	0.7565 — top of the leaderboard
voyage-3-large (Voyage's general model)	No	0.6861
e5-mistral-7b-instruct (general, open)	Yes	0.6449
bge-large-en-v1.5 (general, open)	Yes	0.6436

Vercel is Paid and SQL VEC() Storage is limited
I will use very cheap free model to start and assess it's performance
It's trained on investopedia so I would imagine contrastive fine tuning on title/definition against the content in the body.

I'm going for halfvec