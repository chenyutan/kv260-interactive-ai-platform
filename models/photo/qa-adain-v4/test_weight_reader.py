"""Safety checks for the local restricted legacy-weight adapter."""
import io
import pickle
import unittest

from convert_adain_weights import RestrictedReader, StorageSpec, tensor


class RestrictedWeightReaderTests(unittest.TestCase):
    def test_arbitrary_global_is_rejected_without_resolving_it(self):
        payload = pickle.dumps(eval, protocol=2)
        with self.assertRaises(pickle.UnpicklingError):
            RestrictedReader(io.BytesIO(payload), {}).load()

    def test_valid_contiguous_tensor_descriptor(self):
        storage = StorageSpec('123', 6)
        result = tensor(storage, 0, (2, 3), (3, 1))
        self.assertEqual(result.shape, (2, 3))
        self.assertEqual(result.storage, storage)

    def test_invalid_tensor_descriptors_are_rejected(self):
        storage = StorageSpec('123', 6)
        for offset, shape, stride in ((0, (2, 3), (1, 2)),
                                       (1, (2, 3), (3, 1)),
                                       (0, (2, 3), (3,)),
                                       (0, (0, 3), (3, 1))):
            with self.subTest(offset=offset, shape=shape, stride=stride):
                with self.assertRaises(ValueError):
                    tensor(storage, offset, shape, stride)

    def test_unknown_persistent_storage_type_is_rejected(self):
        reader = RestrictedReader(io.BytesIO(), {})
        with self.assertRaises(ValueError):
            reader.persistent_load(('storage', 'DoubleStorage', '123', 'cpu', 6, None))


if __name__ == '__main__':
    unittest.main()
