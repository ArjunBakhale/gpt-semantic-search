#!/usr/bin/env python

import os
import re
import sys
import argparse
import textwrap
import logging
import warnings

from langchain.chat_models import ChatOpenAI
from langchain.embeddings import OpenAIEmbeddings
from llama_index import LLMPredictor, PromptHelper, ServiceContext, LangchainEmbedding, GPTVectorStoreIndex, ResponseSynthesizer
from llama_index.storage.storage_context import StorageContext
from llama_index.retrievers import VectorIndexRetriever
from llama_index.query_engine import RetrieverQueryEngine
from llama_index.vector_stores import WeaviateVectorStore
from llama_index.vector_stores.types import VectorStoreQueryMode

from slack_sdk import WebClient
import streamlit as st
import weaviate

warnings.simplefilter("ignore", ResourceWarning)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Constants
MAX_INPUT_SIZE = 4096
NUM_OUTPUT = 256
MAX_CHUNK_OVERLAP = 20


def get_unique_nodes(nodes):
    docs_ids = set()
    unique_nodes = list()
    for node in nodes:
        if node.node.ref_doc_id not in docs_ids:
            docs_ids.add(node.node.ref_doc_id)
            unique_nodes.append(node)
    return unique_nodes


def escape_text(text):
    text = re.sub("<", "&lt;", text)
    text = re.sub(">", "&gt;", text)
    text = re.sub("([_#])", "\\\1", text)
    return text


def get_message_link(slack_client, channel, ts):
    res = slack_client.chat_getPermalink(channel=channel, message_ts=ts)
    if res['ok']:
        return res['permalink']
    
    
def main():
    
    parser = argparse.ArgumentParser(description='Web service for semantic search using Weaviate and OpenAI')
    parser.add_argument('-w', '--weaviate-url', type=str, default="http://localhost:8080", help='Weaviate database URL')
    parser.add_argument('-c', '--class-prefix', type=str, default="Janelia", help='Class prefix in Weaviate. The full class name will be "<prefix>_Node".')
    parser.add_argument('-m', '--model', type=str, default="text-curie-001", help='OpenAI model to use for query completion.')
    args = parser.parse_args()
        
    # Based on experimentation, gpt-3.5-turbo does not do well with Slack documents, using text-curie-001 for now. 
    llm = ChatOpenAI(temperature=0.8, model_name=args.model)
    llm_predictor = LLMPredictor(llm=llm)
    embed_model = LangchainEmbedding(OpenAIEmbeddings())
    prompt_helper = PromptHelper(MAX_INPUT_SIZE, NUM_OUTPUT, MAX_CHUNK_OVERLAP)
    service_context = ServiceContext.from_defaults(llm_predictor=llm_predictor, embed_model=embed_model, prompt_helper=prompt_helper)

    # Connect to Weaviate database
    client = weaviate.Client(args.weaviate_url)

    if not client.is_live():
        logger.error(f"Weaviate is not live at {args.weaviate_url}")
        sys.exit()

    if not client.is_live():
        logger.error(f"Weaviate is not ready at {args.weaviate_url}")
        sys.exit()

    vector_store = WeaviateVectorStore(weaviate_client=client, class_prefix=args.class_prefix)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = GPTVectorStoreIndex([], storage_context=storage_context, service_context=service_context)

    slack_client = WebClient(token=os.environ.get('SLACK_TOKEN'))
    res = slack_client.api_test()
    if not res["ok"]:
        logger.error(f"Error initializing Slack API: {res['error']}")
        sys.exit(1)

    # configure retriever
    retriever = VectorIndexRetriever(
        index,
        similarity_top_k=5,
        vector_store_query_mode=VectorStoreQueryMode.HYBRID,
        alpha=0.5,
    )

    # configure response synthesizer
    synth = ResponseSynthesizer.from_args()

    # construct query engine
    query_engine = RetrieverQueryEngine(
        retriever=retriever,
        response_synthesizer=synth,
    )

    st.title("Ask JaneliaGPT")
    query = st.text_input("What would you like to ask?", "")

    if st.button("Submit"):
        if not query.strip():
            st.error(f"Please provide the search query.")
        else:
            try:
                response = query_engine.query(query)
                msg = f"{response.response}\n\nSources:\n\n"
                for node in get_unique_nodes(response.source_nodes):
                    extra_info = node.node.extra_info
                    text = node.node.text
                    
                    text = re.sub("\n+", " ", text)
                    text = textwrap.shorten(text, width=80, placeholder="...")
                    text = escape_text(text)
                    
                    source = extra_info['source']

                    if source == 'slack':
                        channel_id = extra_info['channel']
                        ts = extra_info['ts']
                        msg += f"* [{text}]({get_message_link(slack_client, channel_id, ts)})\n"

                    elif source == 'wiki':
                        msg += f"* [{extra_info['title']}]({extra_info['link']})\n"

                st.success(msg)

            except Exception as e:
                st.error(f"An error occurred: {e}")

    st.markdown("[![Repo](https://badgen.net/badge/icon/GitHub?icon=github&label)](https://github.com/JaneliaSciComp/gpt-semantic-search)")

    hide_footer_style = """
    <style>
    .footer {visibility: hidden;}    
    """
    st.markdown(hide_footer_style, unsafe_allow_html=True)

if __name__ == '__main__':
    main()