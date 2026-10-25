"""Verify Faker API and reproducibility."""

from faker import Faker


def main() -> None:
    Faker.seed(12345)
    fake = Faker("en_IN")
    name1 = fake.name()
    driver_id = f"DRV_{fake.unique.random_number(digits=6, fix_len=True)}"

    Faker.seed(12345)
    fake2 = Faker("en_IN")
    name2 = fake2.name()

    print(f"Faker version check passed. Seeded names match: {name1 == name2} ('{name1}')")
    print(f"Sample Driver ID: {driver_id}")

if __name__ == "__main__":
    main()
