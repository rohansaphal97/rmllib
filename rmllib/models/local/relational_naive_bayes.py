'''
Implements the relational naive bayes model
'''
import pandas
import numpy as np
import scipy

from .base import LocalModel

class RelationalNaiveBayes(LocalModel):
    '''
    Basic RNB implementation.  Can do iid learning to collective inference.
    '''
    def __init__(self, **kwargs):
        '''
        Sets up the NB specific parameters
        '''
        super().__init__(**kwargs)
        self.class_log_prior_ = None
        self.feature_log_prob_ = None


    def predict_proba(self, data, rel_update_only=False):
        '''
        Make predictions

        :param data: Network dataset to make predictions on
        '''
        un_features = data.features.loc[data.mask.Unlabeled, :].T

        if not rel_update_only:
            # Other features taken care of...
            logneg = (un_features.where(un_features != 0, self.feature_log_prob_.loc[0,:].T[0], axis=0)\
                    + un_features.where(un_features != 1, self.feature_log_prob_.loc[0,:].T[1], axis=0)).T.sum(axis=1)
            logpos = (un_features.where(un_features != 0, self.feature_log_prob_.loc[1,:].T[0], axis=0)\
                    + un_features.where(un_features != 1, self.feature_log_prob_.loc[1,:].T[1], axis=0)).T.sum(axis=1)

            base_logits = np.stack((self.class_log_prior_[0] + logneg, self.class_log_prior_[1] + logpos), axis=1)
            self.base_logits = base_logits.copy()

        else:
            base_logits = self.base_logits.copy()

        # IID predictions
        if self.infer_method == 'iid':
            base_conditionals = np.exp(base_logits)

        # Relational IID Predictions
        elif self.infer_method == 'r_iid':
            lab_labels = data.labels.loc[data.mask.Labeled, :]
            lab_to_unlabeled_edges = data.edges.loc[data.mask.Unlabeled, data.mask.Labeled]

            rel = pandas.DataFrame(\
                                    np.hstack((np.dot(lab_to_unlabeled_edges.values, lab_labels.values), np.dot(lab_to_unlabeled_edges.values, 1-lab_labels.values))),\
                                    index=un_features.T.index, columns=['PosN', 'NegN']).rename_axis("X")

            y_logpos = rel['PosN']*self.feature_log_prob_['Y_N'].loc[(1, 1)]\
                            + rel['NegN']*self.feature_log_prob_['Y_N'].loc[(1, 0)]
            y_logneg = rel['PosN']*self.feature_log_prob_['Y_N'].loc[(0, 1)]\
                            + rel['NegN']*self.feature_log_prob_['Y_N'].loc[(0, 0)]

            base_logits += np.stack((y_logneg, y_logpos), axis=1)
            base_conditionals = np.exp(base_logits)

        # Relational Join Predictions
        elif self.infer_method == 'r_joint':
            all_to_unlabeled_edges = data.edges.loc[data.mask.Unlabeled, :].copy() * self.unlabeled_confidence

            rel = pandas.DataFrame(\
                                    np.hstack((np.dot(all_to_unlabeled_edges.values, data.labels.values), np.dot(all_to_unlabeled_edges.values, 1-data.labels.values))),\
                                    index=un_features.T.index, columns=['PosN', 'NegN']).rename_axis("X")

            y_logpos = rel['PosN']*self.feature_log_prob_['Y_N'].loc[(1, 1)]\
                            + rel['NegN']*self.feature_log_prob_['Y_N'].loc[(1, 0)]
            y_logneg = rel['PosN']*self.feature_log_prob_['Y_N'].loc[(0, 1)]\
                            + rel['NegN']*self.feature_log_prob_['Y_N'].loc[(0, 0)]

            base_logits += np.stack((y_logneg, y_logpos), axis=1)
            base_conditionals = np.exp(base_logits)

        # Relational Joint Predictions
        confidence = base_conditionals.sum(axis=1)[:, np.newaxis]
        predictions = base_conditionals / confidence

        if self.calibrate:
            logits = scipy.special.logit(predictions[:, 1])
            logits -= np.percentile(logits, data.labels.loc[data.mask.Labeled].mean()*100)
            predictions[:, 1] = scipy.special.expit(logits)
            predictions[:, 0] = 1 - predictions[:, 1]

        return predictions

    def predict(self, data):
        '''
        Returns the predicted labels on the dataset

        :param data: Network dataset to make predictions on
        '''
        return np.argmax(self.predict_proba(data), axis=1)

    def fit(self, data):
        # Only use the labeled data to fit
        lab_features = data.features.loc[data.mask.Labeled, :]
        lab_labels = data.labels.loc[data.mask.Labeled, :]

        # Prior distributions
        self.class_log_prior_ = np.log([1-lab_labels.Y.mean(), lab_labels.Y.mean()])

        # Compute X log conditional values
        log_posx = np.log(lab_labels.join(lab_features).groupby('Y').mean())
        log_posx['X'] = 1
        log_posx = log_posx.reset_index().set_index(['Y', 'X'])
        log_negx = np.log(1-lab_labels.join(lab_features).groupby('Y').mean())
        log_negx['X'] = 0
        log_negx = log_negx.reset_index().set_index(['Y', 'X'])

        self.feature_log_prob_ = pandas.concat([log_posx, log_negx])

        if self.learn_method == 'r_iid':
            lab_to_lab_edges = data.edges.loc[data.mask.Labeled, data.mask.Labeled]

            # Create basic Y | Y_N counts
            neighbor_counts = pandas.DataFrame(0, index=self.feature_log_prob_.index, columns=['Y_N'])

            neighbor_counts.loc[(1,1), 'Y_N'] = np.sum(np.dot(lab_to_lab_edges.values, lab_labels).flatten() * lab_labels.values.flatten())
            neighbor_counts.loc[(1,0), 'Y_N'] = np.sum(np.dot(lab_to_lab_edges.values, 1-lab_labels).flatten() * lab_labels.values.flatten())
            neighbor_counts.loc[(0,1), 'Y_N'] = np.sum(np.dot(lab_to_lab_edges.values, lab_labels).flatten() * 1-lab_labels.values.flatten())
            neighbor_counts.loc[(0,0), 'Y_N'] = np.sum(np.dot(lab_to_lab_edges.values, 1-lab_labels).flatten() * 1-lab_labels.values.flatten())

            neighbor_counts['Total'] = neighbor_counts.groupby(level=0).transform('sum')
            neighbor_counts['Y_N'] /= neighbor_counts['Total']
            print('R_IID:', neighbor_counts)

            self.feature_log_prob_ = self.feature_log_prob_.join(np.log(neighbor_counts['Y_N']))

        elif self.learn_method == 'r_joint':
            all_to_lab_edges = data.edges.loc[data.mask.Labeled, :].copy() * self.unlabeled_confidence

            # Create basic Y | Y_N conditionals
            neighbor_counts = pandas.DataFrame(0, index=self.feature_log_prob_.index, columns=['Y_N'])

            neighbor_counts.loc[(1,1), 'Y_N'] = np.sum(np.dot(all_to_lab_edges.values, data.labels.Y).flatten() * lab_labels.values.flatten())
            neighbor_counts.loc[(1,0), 'Y_N'] = np.sum(np.dot(all_to_lab_edges.values, 1-data.labels.Y).flatten() * lab_labels.values.flatten())
            neighbor_counts.loc[(0,1), 'Y_N'] = np.sum(np.dot(all_to_lab_edges.values, data.labels.Y).flatten() * 1-lab_labels.values.flatten())
            neighbor_counts.loc[(0,0), 'Y_N'] = np.sum(np.dot(all_to_lab_edges.values, 1-data.labels.Y).flatten() * 1-lab_labels.values.flatten())

            neighbor_counts['Total'] = neighbor_counts.groupby(level=0).transform('sum')
            neighbor_counts['Y_N'] /= neighbor_counts['Total']

            self.feature_log_prob_ = self.feature_log_prob_.join(np.log(neighbor_counts['Y_N']))
            

        return self
