from metaflow import FlowSpec, step, S3, Parameter, current, card
from metaflow.cards import Markdown, Table
from random import choice
import os
import json
import time

class DataFlow(FlowSpec):

    IS_DEV = Parameter(
        name='is_dev',
        help='Flag for dev development, with a smaller dataset',
        default='0'
    )

    KNN_K = Parameter(
        name='knn_k',
        help='Number of neighbors we retrieve from the vector space',
        default='50'
    ) 
    
    @step
    def start(self):
        self.next(self.prepare_dataset)

    @step
    def prepare_dataset(self):
        """
        Get the data in the right shape by reading the parquet dataset
        and using DuckDB SQL-based wrangling to quickly prepare the datasets for
        training our Recommender System.
        """
        import duckdb
        import numpy as np
        con = duckdb.connect(database=':memory:')
        con.execute("""
            CREATE TABLE orders AS 
            SELECT *, 
            CONCAT (user_id, '-', order_id) as order_id,
            CONCAT (res_id, '|||', item) as item_id,
            FROM 'recommendnameData.parquet'
            ;
        """)
        con.execute("SELECT * FROM orders LIMIT 1;")
        print(con.fetchone())
        tables = ['row_id', 'user_id', 'item_id', 'order_id', 'res_id']
        for t in tables:
            con.execute("SELECT COUNT(DISTINCT({})) FROM orders;".format(t))
            print("# of {}".format(t), con.fetchone()[0])
        sampling_cmd = ''
        if self.IS_DEV == '1':
            print("Subsampling data, since this is DEV")
            sampling_cmd = ' USING SAMPLE 10 PERCENT (bernoulli)'
        dataset_query = """
            SELECT * FROM
            (   
                SELECT 
                    order_id,
                    LIST(res_id ORDER BY row_id ASC) as res_sequence,
                    LIST(item_id ORDER BY row_id ASC) as item_sequence,
                    array_pop_back(LIST(item_id ORDER BY row_id ASC)) as item_test_x,
                    LIST(item_id ORDER BY row_id ASC)[-1] as item_test_y
                FROM 
                    orders
                GROUP BY order_id 
                HAVING len(item_sequence) > 2
            ) 
            {}
            ;
            """.format(sampling_cmd)
        con.execute(dataset_query)
        df = con.fetch_df()
        print("# rows: {}".format(len(df)))
        print(df.iloc[0].tolist())
        con.close()
        train, validate, test = np.split(
            df.sample(frac=1, random_state=42), 
            [int(.7 * len(df)), int(.9 * len(df))])
        self.df_dataset = df
        self.df_train = train
        self.df_validate = validate
        self.df_test = test
        print("# testing rows: {}".format(len(self.df_test)))

        self.hypers_sets = [json.dumps(_) for _ in [
            { 'min_count': 5, 'epochs': 60, 'vector_size': 48, 'window': 10, 'ns_exponent': 0.75 },
            { 'min_count': 10, 'epochs': 60, 'vector_size': 48, 'window': 10, 'ns_exponent': 0.75 }
        ]]
        
        self.next(self.generate_embeddings, foreach='hypers_sets')
    
    def predict_next_track(self, vector_space, input_sequence, k):
        """        
        Given an embedding space, predict best next song with KNN.
        Initially, we just take the LAST item in the input playlist as the query item for KNN
        and retrieve the top K nearest vectors (you could think of taking the smoothed average embedding
        of the input list, for example, as a refinement).

        If the query item is not in the vector space, we make a random bet. We could refine this by taking
        for example the vector of the artist (average of all songs), or with some other strategy (sampling
        by popularity). 

        For more options on how to generate vectors for "cold items" see for example the paper:
        https://dl.acm.org/doi/10.1145/3383313.3411477
        """
        query_item = input_sequence[-1]
        if query_item not in vector_space:
            query_item = choice(list(vector_space.index_to_key))
        
        return [_[0] for _ in vector_space.most_similar(query_item, topn=k)]

    def evaluate_model(self, _df, vector_space, k):
        lambda_predict = lambda row: self.predict_next_track(vector_space, row['item_test_x'], k)
        _df['predictions'] = _df.apply(lambda_predict, axis=1)
        lambda_hit = lambda row: 1 if row['item_test_y'] in row['predictions'] else 0
        _df['hit'] = _df.apply(lambda_hit, axis=1)
        hit_rate = _df['hit'].sum() / len(_df)
        return hit_rate
    
    @step
    def generate_embeddings(self):
        """
        Generate vector representations for songs, based on the Prod2Vec idea.

        For an overview of the algorithm and the evaluation, see for example:
        https://arxiv.org/abs/2007.14906
        """
        from gensim.models.word2vec import Word2Vec
        self.hyper_string = self.input
        self.hypers = json.loads(self.hyper_string)
        track2vec_model = Word2Vec([list(i) for i in self.df_train['item_sequence']], **self.hypers)
        print("Training with hypers {} is completed!".format(self.hyper_string))
        print("Vector space size: {}".format(len(track2vec_model.wv.index_to_key)))
        test_track = choice(list(track2vec_model.wv.index_to_key))
        print("Example track: '{}'".format(test_track))
        test_vector = track2vec_model.wv[test_track]
        print("Test vector for '{}': {}".format(test_track, test_vector[:5]))
        test_sims = track2vec_model.wv.most_similar(test_track, topn=3)
        print("Similar songs to '{}': {}".format(test_track, test_sims))
        self.validation_metric = self.evaluate_model(
            self.df_validate,
            track2vec_model.wv,
            k=int(self.KNN_K))
        print("Hit Rate@{} is: {}".format(self.KNN_K, self.validation_metric))
        self.track_vectors = track2vec_model.wv
        self.next(self.join_runs)

    @card(type='blank', id='hyperCard')
    @step
    def join_runs(self, inputs):
        """
        Join the parallel runs and merge results into a dictionary.
        """
        self.all_vectors = { inp.hyper_string: inp.track_vectors for inp in inputs}
        self.all_results = { inp.hyper_string: inp.validation_metric for inp in inputs}
        print("Current result map: {}".format(self.all_results))
        self.best_model, self_best_result = sorted(self.all_results.items(), key=lambda x: x[1], reverse=True)[0]
        print("The best validation score is for model: {}, {}".format(self.best_model, self_best_result))
        self.final_vectors = self.all_vectors[self.best_model]
        self.final_dataset = inputs[0].df_test
        current.card.append(Markdown("## Results from parallel training"))
        current.card.append(
            Table([
                [inp.hyper_string, inp.validation_metric] for inp in inputs
            ])
        )
        # next, test the best model on unseen data, and report the final Hit Rate as 
        # our best point-wise estimate of "in the wild" performance
        self.next(self.model_testing)

    @step
    def model_testing(self):
        """
        Test the generalization abilities of the best model by running predictions
        on the unseen test data.

        We report a quantitative point-wise metric, hit rate @ K, as an initial implementation. However,
        evaluating recommender systems is a very complex task, and better metrics, through good abstractions, 
        are available, i.e. https://reclist.io/.
        """
        self.test_metric = self.evaluate_model(
            self.final_dataset,
            self.final_vectors,
            k=int(self.KNN_K))
        print("Hit Rate@{} on the test set is: {}".format(self.KNN_K, self.test_metric))
        self.next(self.end)

    @step
    def end(self):
        pass

if __name__ == '__main__':
    DataFlow()