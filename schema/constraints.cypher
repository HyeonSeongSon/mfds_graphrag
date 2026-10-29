// mfds-graphrag 제약 · 인덱스 (schema/schema.yaml과 같은 라벨). 여러 번 실행해도 된다(IF NOT EXISTS).
// 벡터 인덱스는 임베딩 적재 뒤 1주차 D5에 따로 만든다.
CREATE CONSTRAINT law_uid IF NOT EXISTS FOR (n:Law) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT article_uid IF NOT EXISTS FOR (n:Article) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT paragraph_uid IF NOT EXISTS FOR (n:Paragraph) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT item_uid IF NOT EXISTS FOR (n:Item) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT annex_uid IF NOT EXISTS FOR (n:Annex) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT adminrule_uid IF NOT EXISTS FOR (n:AdminRule) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT adminarticle_uid IF NOT EXISTS FOR (n:AdminArticle) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT case_uid IF NOT EXISTS FOR (n:Case) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT interpretation_uid IF NOT EXISTS FOR (n:Interpretation) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT adminappeal_uid IF NOT EXISTS FOR (n:AdminAppeal) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT disposition_uid IF NOT EXISTS FOR (n:Disposition) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT ingredient_uid IF NOT EXISTS FOR (n:Ingredient) REQUIRE n.uid IS UNIQUE;
CREATE CONSTRAINT durrule_uid IF NOT EXISTS FOR (n:DurRule) REQUIRE n.uid IS UNIQUE;
CREATE INDEX law_name IF NOT EXISTS FOR (n:Law) ON (n.name);
CREATE INDEX ingredient_name IF NOT EXISTS FOR (n:Ingredient) ON (n.name);
CREATE INDEX durrule_type IF NOT EXISTS FOR (n:DurRule) ON (n.type);
CREATE INDEX disposition_industry IF NOT EXISTS FOR (n:Disposition) ON (n.industry);
CREATE FULLTEXT INDEX ingredient_alias IF NOT EXISTS FOR (n:Ingredient) ON EACH [n.name, n.aliases, n.base];
